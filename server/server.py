"""Sotto server.

One stdlib-only process with two faces:

* an MCP server on stdin/stdout for an AI agent such as Claude Code, Gemini CLI or Codex, and
* a loopback HTTP endpoint the Sotto Chrome extension posts the Meet
  tab's captions, status and shared-screen frames to.

The endpoint opens only when the agent calls `start`, so sessions that never
use the copilot never hold the port. When a second session calls `start`, it
takes the meeting over from the first, transcript included.
"""

from __future__ import annotations

import base64
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import wait as wait_for_futures
from datetime import datetime
from pathlib import Path

VERSION = "0.1.0"
PORT = int(os.environ.get("SOTTO_PORT", "47823"))
SAVE_DIR = Path(os.environ.get("SOTTO_DIR") or Path.home() / "Sotto")
EXTENSION_DIR = Path(__file__).resolve().parent.parent / "extension"

QUIET_S = 1.2        # a caption still being spoken is delivered once it stops changing for this long
MAX_HOLD_S = 10.0    # ...or once it has waited this long, so a monologue still streams in
STALE_S = 8.0        # no report from the Meet tab for this long means the tab is gone
REALIGN_S = 15.0     # after Meet rebuilds its caption list, re-sent rows are matched to known turns
MAX_BODY = 8_000_000
MAX_TRANSCRIPT_CHARS = 80_000


def log(*parts):
    print("[sotto]", *parts, file=sys.stderr, flush=True)


def clock(ts):
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def tidy(value):
    return " ".join(str(value or "").split())


def text_result(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


class Turn:
    """One speaker turn as Meet shows it. Its text keeps growing while it is the bottom row."""

    FIELDS = ("speaker", "text", "started", "changed", "live", "delivered", "pending_since", "saved")

    def __init__(self, speaker, text, now):
        self.speaker = speaker
        self.text = text
        self.started = now
        self.changed = now
        self.live = False
        self.delivered = ""
        self.pending_since = now
        self.saved = False

    def to_dict(self):
        return {name: getattr(self, name) for name in self.FIELDS}

    @classmethod
    def from_dict(cls, data):
        turn = cls(data["speaker"], data["text"], data["started"])
        for name in cls.FIELDS:
            if name in data:
                setattr(turn, name, data[name])
        return turn


class Meeting:
    """What the copilot knows about the current call, shared by the HTTP and MCP threads."""

    def __init__(self):
        self.cond = threading.Condition()
        self.lost = False
        self._reset(None)

    def _reset(self, code):
        self.code = code
        self.title = ""
        self.turns = []
        self.keys = {}
        self.frames = []
        self.frames_seen = 0
        self.want_frame = False
        self.no_video_at = 0.0
        self.captions = "unknown"
        self.in_call = False
        self.presenter = None
        self.last_report = 0.0
        self.connected = False
        self.notices = []
        self.source = None
        self.realign_until = 0.0
        self.folder = None

    # Reports from the extension.

    def on_report(self, body):
        now = time.time()
        with self.cond:
            self._follow(body)
            if not self.connected:
                self.connected = True
                self.notices.append(f"Meet tab connected: {self._label()}.")
            self.last_report = now
            self._note_captions(body.get("captions") or "unknown")
            in_call = bool(body.get("inCall"))
            if self.in_call and not in_call:
                self.notices.append("The user left the call.")
            self.in_call = in_call
            self._note_presenter(tidy(body.get("presenter")) or None)
            self.cond.notify_all()
            return {"ok": True, "wantFrame": self.want_frame}

    def on_captions(self, body):
        now = time.time()
        with self.cond:
            self._follow(body)
            source = (str(body.get("sid") or ""), int(body.get("epoch") or 0))
            if source != self.source:
                if self.source is not None:
                    self.realign_until = now + REALIGN_S
                self.source = source
            prefix = f"{source[0]}.{source[1]}:"
            for row in (body.get("rows") or [])[-80:]:
                text = tidy(row.get("text"))[:6000]
                if not text:
                    continue
                key = prefix + str(row.get("key"))
                turn = self.keys.get(key)
                if turn is None:
                    speaker = tidy(row.get("speaker"))[:80] or "Unknown"
                    turn = self._realigned(speaker, text) if now < self.realign_until else None
                    if turn is None:
                        turn = Turn(speaker, text, now)
                        self.turns.append(turn)
                    self.keys[key] = turn
                if text != turn.text:
                    turn.text = text
                    turn.changed = now
                    if turn.pending_since is None:
                        turn.pending_since = now
            bottom = self.keys.get(prefix + str(body.get("bottomKey")))
            for turn in self.turns[-40:]:
                turn.live = turn is bottom
            self._save(final=False)
            self.cond.notify_all()
            return {"ok": True}

    def on_frame(self, body):
        now = time.time()
        with self.cond:
            self._follow(body)
            self.want_frame = False
            if body.get("none"):
                self.no_video_at = now
                self.cond.notify_all()
                return {"ok": True}
            data = base64.b64decode(body.get("jpeg") or "")
            if not data:
                return {"ok": False}
            frame_id = len(self.frames) + 1
            folder = self._folder() / "frames"
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{frame_id:03d}.jpg"
            path.write_bytes(data)
            self.frames.append({
                "id": frame_id,
                "at": now,
                "path": str(path),
                "w": int(body.get("w") or 0),
                "h": int(body.get("h") or 0),
                "reason": body.get("reason") or "changed",
                "presenter": self.presenter,
            })
            self.cond.notify_all()
            return {"ok": True, "id": frame_id}

    # Reads for the MCP tools.

    def wait(self, timeout):
        deadline = time.time() + timeout
        with self.cond:
            while True:
                if self.lost:
                    return ["[status] Another session took over this meeting. Stop listening here."]
                now = time.time()
                lines = self._collect(now)
                if lines:
                    return lines
                if now >= deadline:
                    return []
                self.cond.wait(min(deadline - now, 0.25))

    def screen(self, frame_id=None, wait_s=6.0):
        with self.cond:
            if frame_id is not None:
                frame = next((f for f in self.frames if f["id"] == frame_id), None)
                return frame, None if frame else f"There is no frame {frame_id}."
            if not self._live_tab(time.time()):
                if self.frames:
                    return self.frames[-1], "The Meet tab isn't connected; this is the last saved frame."
                return None, "The Meet tab isn't connected, so there's nothing to capture."
            asked = time.time()
            count = len(self.frames)
            self.want_frame = True
            while len(self.frames) == count and self.no_video_at < asked and time.time() < asked + wait_s:
                self.cond.wait(0.25)
            if len(self.frames) > count:
                self.frames_seen = len(self.frames)
                return self.frames[-1], None
            self.want_frame = False
            if self.no_video_at >= asked:
                return None, "There's no video in the Meet tab to capture right now."
            return None, "The Meet tab didn't send a frame in time; try again."

    def transcript(self, minutes):
        with self.cond:
            since = time.time() - minutes * 60 if minutes else 0
            lines = [f"{clock(t.started)} {t.speaker}: {t.text}" for t in self.turns if t.started >= since]
            folder = self.folder
        text = "\n".join(lines)
        if len(text) > MAX_TRANSCRIPT_CHARS:
            where = folder / "transcript.md" if folder else "the meeting folder"
            text = f"[earlier part trimmed; the full transcript is in {where}]\n" + text[-MAX_TRANSCRIPT_CHARS:]
        return text or "(nothing has been said yet)"

    def status(self):
        with self.cond:
            lines = []
            if self._live_tab(time.time()):
                state = [f"Meet tab connected: {self._label()}", "in the call" if self.in_call else "not in the call yet"]
                state.append({
                    "on": "captions on",
                    "off": "captions OFF (ask the user to press c; only they see captions)",
                }.get(self.captions, "captions state not known yet"))
                if self.presenter:
                    state.append(f"{self.presenter} is presenting")
                lines.append(", ".join(state) + ".")
            else:
                # An agent's plugin cache is replaced on every update, so don't send the user there.
                cached = "cache" in (part.lower() for part in EXTENSION_DIR.parts)
                folder = "the extension folder of their Sotto copy" if cached else str(EXTENSION_DIR)
                lines.append(
                    "Meet tab not connected. Ask the user to open the meeting in Chrome with the Sotto "
                    f"extension enabled (one-time setup: chrome://extensions, Developer mode, Load unpacked, {folder})."
                )
            lines.append(f"So far: {len(self.turns)} caption turns, {len(self.frames)} screen frames.")
            if self.folder:
                lines.append(f"Saving to {self.folder}")
            return "\n".join(lines)

    def is_connected(self):
        with self.cond:
            return self._live_tab(time.time())

    # Handing the meeting between sessions, and finishing it.

    def export(self):
        with self.cond:
            index = {id(turn): i for i, turn in enumerate(self.turns)}
            return {
                "code": self.code,
                "title": self.title,
                "folder": str(self.folder) if self.folder else None,
                "turns": [turn.to_dict() for turn in self.turns],
                "keys": {key: index[id(turn)] for key, turn in self.keys.items() if id(turn) in index},
                "source": list(self.source) if self.source else None,
                "frames": self.frames,
            }

    def adopt(self, data):
        with self.cond:
            self._reset(data.get("code"))
            self.title = data.get("title") or ""
            self.folder = Path(data["folder"]) if data.get("folder") else None
            self.turns = [Turn.from_dict(item) for item in data.get("turns") or []]
            self.keys = {key: self.turns[i] for key, i in (data.get("keys") or {}).items() if 0 <= i < len(self.turns)}
            self.source = tuple(data["source"]) if data.get("source") else None
            self.frames = list(data.get("frames") or [])
            self.frames_seen = len(self.frames)
            for turn in self.turns:
                # This session starts listening from here; earlier speech is in get_transcript.
                turn.delivered = turn.text
                turn.pending_since = None
            self.lost = False

    def release(self):
        with self.cond:
            self.lost = True
            self.cond.notify_all()

    def finish(self):
        with self.cond:
            self._save(final=True)
            return self.folder

    # Internals; callers hold self.cond.

    def _follow(self, body):
        code = tidy(body.get("meeting")) or None
        if code and code != self.code:
            switching = self.code is not None
            self._reset(code)
            if switching:
                self.notices.append(f"Now following a different meeting: {code}.")
        title = tidy(body.get("title"))
        if title:
            self.title = title[:120]

    def _note_captions(self, state):
        if state == self.captions:
            return
        if state == "off":
            self.notices.append("Captions are OFF in the Meet tab, so nothing is being heard. Ask the user to turn them on (press c).")
        elif state == "on" and self.captions == "off":
            self.notices.append("Captions are back on.")
        self.captions = state

    def _note_presenter(self, presenter):
        if presenter == self.presenter:
            return
        if presenter:
            self.notices.append(f"{presenter} started presenting.")
        else:
            self.notices.append(f"{self.presenter} stopped presenting.")
        self.presenter = presenter

    def _realigned(self, speaker, text):
        latest = True
        for turn in reversed(self.turns[-40:]):
            if turn.speaker != speaker:
                continue
            if turn.text == text:
                return turn
            if latest and (turn.text.startswith(text) or text.startswith(turn.text)):
                return turn
            latest = False
        return None

    def _collect(self, now):
        if self.connected and now - self.last_report > STALE_S:
            self.connected = False
            self.notices.append("The Meet tab stopped reporting (closed, reloaded, or the extension is off).")
        out = [f"[status] {notice}" for notice in self.notices]
        self.notices.clear()
        for turn in self.turns:
            if turn.text == turn.delivered:
                continue
            if turn.live and now - turn.changed < QUIET_S and now - (turn.pending_since or now) < MAX_HOLD_S:
                continue
            line = self._line(turn)
            if line:
                out.append(line)
            turn.delivered = turn.text
            turn.pending_since = None
        if len(self.frames) > self.frames_seen:
            latest = self.frames[-1]
            fresh = len(self.frames) - self.frames_seen
            whose = f"{latest['presenter']}'s shared screen" if latest.get("presenter") else "The shared screen"
            more = f" ({fresh} new frames)" if fresh > 1 else ""
            out.append(
                f"[screen] {whose} changed{more}: frame {latest['id']} at {clock(latest['at'])}. "
                f"Call get_screen with frame={latest['id']} to look at it."
            )
            self.frames_seen = len(self.frames)
        return out

    def _line(self, turn):
        stamp = f"{clock(turn.started)} {turn.speaker}:"
        if not turn.delivered:
            return f"{stamp} {turn.text}"
        old, new = turn.delivered.split(), turn.text.split()
        same = 0
        while same < min(len(old), len(new)) and old[same] == new[same]:
            same += 1
        if same >= len(old) - 2:
            # Only the tail moved: more speech, or Meet refining its last few words.
            rest = " ".join(new[same:])
            return f"{stamp} … {rest}" if rest else None
        return f"{clock(turn.started)} {turn.speaker} (corrected): {turn.text}"

    def _live_tab(self, now):
        return self.connected and now - self.last_report <= STALE_S

    def _label(self):
        code = self.code or "a meeting"
        return f'{code} "{self.title}"' if self.title and code not in self.title else self.title or code

    def _folder(self):
        if self.folder is None:
            stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
            self.folder = SAVE_DIR / "meetings" / f"{stamp}_{self.code or 'meet'}"
            self.folder.mkdir(parents=True, exist_ok=True)
        return self.folder

    def _save(self, final):
        lines = []
        for turn in self.turns:
            if turn.saved:
                continue
            if turn.live and not final:
                break
            lines.append(f"**{clock(turn.started)} {turn.speaker}:** {turn.text}\n\n")
            turn.saved = True
        if not lines:
            return
        with (self._folder() / "transcript.md").open("a", encoding="utf-8") as out:
            if out.tell() == 0:
                out.write(f"# {self.title or self.code or 'Meeting'}\n\n")
            out.writelines(lines)


class LoopbackServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    # SO_REUSEADDR lets a restarted copilot rebind past lingering connections on Linux and macOS. On
    # Windows it would let two sessions share the port, and Windows rebinds without it.
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)  # skips HTTPServer's reverse-DNS lookup of the address
        self.server_name, self.server_port = self.server_address[:2]


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = f"Sotto/{VERSION}"

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/api/health" and self._allowed():
            return self._send(200, {"ok": True, "app": "sotto", "version": VERSION})
        self._send(404, {"ok": False})

    def do_POST(self):
        routes = {
            "/ext/captions": MEETING.on_captions,
            "/ext/report": MEETING.on_report,
            "/ext/frame": MEETING.on_frame,
            "/api/handover": ENDPOINT.hand_over,
        }
        route = routes.get(self.path)
        if route is None:
            return self._send(404, {"ok": False})
        if not self._allowed():
            return self._send(403, {"ok": False})
        try:
            size = int(self.headers.get("Content-Length") or 0)
            if size > MAX_BODY:
                return self._send(413, {"ok": False})
            body = json.loads(self.rfile.read(size) or b"{}")
            self._send(200, route(body))
        except Exception as exc:
            log("request failed:", self.path, repr(exc))
            self._send(400, {"ok": False})

    def _allowed(self):
        # A web page can't add this header to a cross-origin request without a CORS preflight, which this
        # server never answers, so only the extension and local programs get through.
        if self.headers.get("X-Sotto") != "1":
            return False
        origin = self.headers.get("Origin") or ""
        if self.path.startswith("/api/"):
            return origin == ""
        return origin == "" or origin.startswith("chrome-extension://")

    def _send(self, code, payload):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class Endpoint:
    """The loopback port the extension reports to, owned by at most one session at a time."""

    def __init__(self):
        self.httpd = None

    @property
    def open(self):
        return self.httpd is not None

    def start(self):
        if self.httpd:
            return None
        try:
            self._bind()
            return None
        except OSError:
            pass
        handed = self._take_over()
        if handed is None:
            raise RuntimeError(
                f"Port {PORT} is held by another program. Set SOTTO_PORT, and the port in "
                "extension/background.js, to a free one."
            )
        for _ in range(30):
            try:
                self._bind()
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("The other session released the meeting but the port did not free up. Call start again.")
        MEETING.adopt(handed)
        return "Took the meeting over from another session."

    def hand_over(self, _body):
        state = MEETING.export()
        MEETING.release()
        threading.Timer(0.15, self.close).start()
        return {"ok": True, "meeting": state}

    def close(self):
        httpd, self.httpd = self.httpd, None
        if httpd:
            httpd.shutdown()
            httpd.server_close()

    def _bind(self):
        httpd = LoopbackServer(("127.0.0.1", PORT), Handler)
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.25}, daemon=True).start()
        self.httpd = httpd
        MEETING.lost = False

    def _take_over(self):
        request = urllib.request.Request(
            f"http://127.0.0.1:{PORT}/api/handover",
            data=b"{}",
            method="POST",
            headers={"Content-Type": "application/json", "X-Sotto": "1"},
        )
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                data = json.loads(response.read())
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return data.get("meeting") if data.get("ok") else None


MEETING = Meeting()
ENDPOINT = Endpoint()


# MCP tools.

def tool_start(_args):
    note = ENDPOINT.start()
    parts = (f"Sotto is listening on 127.0.0.1:{PORT}.", note, MEETING.status())
    return text_result("\n".join(part for part in parts if part))


def tool_wait(args):
    if MEETING.lost:
        return text_result("Another session took over this meeting. Stop listening here.")
    if not ENDPOINT.open:
        return text_result("Sotto isn't running in this session. Call start first.", error=True)
    timeout = min(max(int(args.get("timeout_s") or 45), 5), 55)
    lines = MEETING.wait(timeout)
    if lines:
        return text_result("\n".join(lines))
    return text_result("(no new speech)" if MEETING.is_connected() else "(no new speech; the Meet tab is not connected)")


def tool_get_screen(args):
    frame_id = args.get("frame")
    frame_id = int(frame_id) if frame_id is not None else None
    if frame_id is None and not ENDPOINT.open:
        return text_result("Sotto isn't running in this session. Call start first.", error=True)
    frame, note = MEETING.screen(frame_id)
    if frame is None:
        return text_result(note)
    data = base64.b64encode(Path(frame["path"]).read_bytes()).decode("ascii")
    whose = f"{frame['presenter']}'s shared screen" if frame.get("presenter") else "the Meet tab"
    caption = f"Frame {frame['id']} at {clock(frame['at'])}, {whose}, {frame['w']}x{frame['h']}."
    if note:
        caption += f" {note}"
    return {
        "content": [{"type": "text", "text": caption}, {"type": "image", "data": data, "mimeType": "image/jpeg"}],
        "isError": False,
    }


def tool_get_transcript(args):
    return text_result(MEETING.transcript(float(args.get("last_minutes") or 0)))


def tool_stop(_args):
    folder = MEETING.finish()
    ENDPOINT.close()
    if folder:
        return text_result(f"Stopped listening. Transcript and screen frames are saved in {folder}")
    return text_result("Stopped listening. Nothing was recorded.")


# MCP tool annotations. Agents that auto-approve read-only tools (Codex, for one) can then run the
# listening loop without asking each time; start and stop only touch Sotto's own local state.
READS_MEETING = {"readOnlyHint": True, "openWorldHint": False}
LOCAL_STATE = {"readOnlyHint": False, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}

TOOLS = [
    {
        "name": "start",
        "description": (
            "Start Sotto, or check on it. Opens the local endpoint the Sotto Chrome "
            "extension reports to and says what is connected: the Meet tab, whether captions are on, who is "
            "presenting. Safe to call again at any time."
        ),
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": LOCAL_STATE,
    },
    {
        "name": "wait_for_update",
        "description": (
            "The listening loop. Blocks until something new happens in the meeting, then returns it: caption "
            "lines as 'HH:MM:SS Speaker: text' (delivered once the speaker pauses; 'You' is the user, and a "
            "line starting with '…' continues that speaker's turn), [screen] notices when the shared screen "
            "changes, and [status] notices such as captions turned off or the call ending. Returns "
            "'(no new speech)' when the timeout passes quietly."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_s": {"type": "integer", "minimum": 5, "maximum": 55, "description": "Seconds to wait (default 45)."},
            },
        },
        "annotations": READS_MEETING,
    },
    {
        "name": "get_screen",
        "description": (
            "Look at the shared screen. With a frame id from a [screen] notice, returns that saved frame. "
            "Without one, asks the Meet tab for a fresh snapshot of its largest video and returns it."
        ),
        "inputSchema": {"type": "object", "properties": {"frame": {"type": "integer", "minimum": 1}}},
        "annotations": READS_MEETING,
    },
    {
        "name": "get_transcript",
        "description": (
            "Everything said so far, or only the last N minutes. For recovering context or writing a "
            "summary; the listening loop doesn't need it."
        ),
        "inputSchema": {"type": "object", "properties": {"last_minutes": {"type": "number", "minimum": 0}}},
        "annotations": READS_MEETING,
    },
    {
        "name": "stop",
        "description": "Stop listening and close the local endpoint. Returns where the transcript and frames were saved.",
        "inputSchema": {"type": "object", "properties": {}},
        "annotations": LOCAL_STATE,
    },
]

HANDLERS = {
    "start": tool_start,
    "wait_for_update": tool_wait,
    "get_screen": tool_get_screen,
    "get_transcript": tool_get_transcript,
    "stop": tool_stop,
}


def serve_stdio():
    lock = threading.Lock()

    def send(message):
        data = (json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8")
        with lock:
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()

    def handle(message):
        request_id, method = message.get("id"), message.get("method")
        if request_id is None or method is None:
            return  # notifications, and replies to requests we never send
        params = message.get("params") or {}
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": params.get("protocolVersion") or "2025-06-18",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "sotto", "version": VERSION},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                tool = HANDLERS.get(params.get("name"))
                if tool is None:
                    result = text_result(f"Unknown tool: {params.get('name')}", error=True)
                else:
                    try:
                        result = tool(params.get("arguments") or {})
                    except Exception as exc:
                        log("tool failed:", params.get("name"), repr(exc))
                        result = text_result(str(exc), error=True)
            else:
                send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": f"Method not found: {method}"}})
                return
            send({"jsonrpc": "2.0", "id": request_id, "result": result})
        except Exception as exc:
            log("request failed:", method, repr(exc))
            send({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": str(exc)}})

    workers = ThreadPoolExecutor(max_workers=8)
    in_flight = set()
    while True:
        raw = sys.stdin.buffer.readline()
        if not raw:
            break
        raw = raw.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except ValueError:
            log("ignored a line that isn't JSON")
            continue
        if isinstance(message, dict):
            future = workers.submit(handle, message)
            in_flight.add(future)
            future.add_done_callback(in_flight.discard)
    ENDPOINT.close()
    wait_for_futures(list(in_flight), timeout=2)  # let quick replies go out
    os._exit(0)  # the client is gone; don't wait out a long-poll still parked in a worker


if __name__ == "__main__":
    try:
        serve_stdio()
    except KeyboardInterrupt:
        pass
