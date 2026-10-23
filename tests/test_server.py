"""End-to-end checks for server/server.py: driven over MCP stdio the way Claude Code drives it, and
posted to the way the Chrome extension posts to it."""

import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent / "server" / "server.py"
JPEG = b"\xff\xd8\xff\xe0 not really a picture \xff\xd9"
TAB = {"sid": "test", "meeting": "abc-defg-hij", "title": "Pricing call"}


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class Session:
    """One server process, as one Claude Code session sees it."""

    def __init__(self, port, save_dir, log_path):
        env = dict(os.environ, SOTTO_PORT=str(port), SOTTO_DIR=str(save_dir))
        self.log = open(log_path, "ab")
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, env=env
        )
        self.ids = 0
        self.id_lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.replies = {}
        self.arrived = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()
        hello = self.request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        assert hello["result"]["serverInfo"]["name"] == "sotto"
        self._write({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def _read(self):
        for line in self.proc.stdout:
            message = json.loads(line)
            with self.arrived:
                self.replies[message["id"]] = message
                self.arrived.notify_all()

    def _write(self, message):
        with self.write_lock:
            self.proc.stdin.write((json.dumps(message) + "\n").encode())
            self.proc.stdin.flush()

    def request(self, method, params=None, timeout=70):
        with self.id_lock:
            self.ids += 1
            request_id = self.ids
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        with self.arrived:
            while request_id not in self.replies:
                left = deadline - time.time()
                if left <= 0:
                    raise TimeoutError(method)
                self.arrived.wait(left)
            return self.replies.pop(request_id)

    def call(self, tool, **arguments):
        return self.request("tools/call", {"name": tool, "arguments": arguments})["result"]

    def text(self, tool, **arguments):
        return "\n".join(part["text"] for part in self.call(tool, **arguments)["content"] if part["type"] == "text")

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        self.proc.stdout.close()
        self.log.close()


def post(port, path, body, headers=None, bare=False):
    sent = {"Content-Type": "application/json"} if bare else {"Content-Type": "application/json", "X-Sotto": "1"}
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method="POST", headers={**sent, **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, None


def report(port, **fields):
    return post(port, "/ext/report", {**TAB, "captions": "on", "inCall": True, "presenter": None, **fields})


def captions(port, rows, epoch=0):
    keyed, counts = [], {}
    for speaker, text in rows:
        counts[speaker] = counts.get(speaker, 0) + 1
        keyed.append({"key": f"{speaker}#{counts[speaker]}", "speaker": speaker, "text": text})
    return post(port, "/ext/captions", {**TAB, "epoch": epoch, "bottomKey": keyed[-1]["key"], "rows": keyed})


def frame(port, reason="changed"):
    return post(port, "/ext/frame", {**TAB, "reason": reason, "w": 1568, "h": 882, "jpeg": base64.b64encode(JPEG).decode()})


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.port = free_port()
        self.sessions = []
        self.reporting = threading.Event()

    def tearDown(self):
        self.reporting.set()
        for session in self.sessions:
            session.close()
        self.tmp.cleanup()

    def session(self):
        session = Session(self.port, Path(self.tmp.name) / "saved", Path(self.tmp.name) / "server.log")
        self.sessions.append(session)
        return session

    def keep_reporting(self, **fields):
        def loop():
            while not self.reporting.wait(1):
                try:
                    report(self.port, **fields)
                except urllib.error.URLError:
                    pass
        threading.Thread(target=loop, daemon=True).start()

    def test_lists_its_tools(self):
        tools = {tool["name"] for tool in self.session().request("tools/list")["result"]["tools"]}
        self.assertEqual(tools, {"start", "wait_for_update", "get_screen", "get_transcript", "stop"})

    def test_port_opens_only_after_start(self):
        copilot = self.session()
        with self.assertRaises(urllib.error.URLError):
            report(self.port)
        self.assertIn("Meet tab not connected", copilot.text("start"))
        self.assertEqual(report(self.port)[1], {"ok": True, "wantFrame": False})
        self.assertIn("captions on", copilot.text("start"))

    def test_delivers_each_turn_once_its_speaker_pauses(self):
        copilot = self.session()
        copilot.text("start")
        report(self.port)
        self.keep_reporting()
        captions(self.port, [("Rahul", "can we close pricing today"), ("You", "maybe")])
        first = copilot.text("wait_for_update", timeout_s=5)
        self.assertIn("[status] Meet tab connected", first)
        self.assertIn("Rahul: can we close pricing today", first)
        self.assertNotIn("You:", first)
        self.assertIn("You: maybe", copilot.text("wait_for_update", timeout_s=5))
        captions(self.port, [("Rahul", "can we close pricing today"), ("You", "maybe next week")])
        self.assertIn("You: … next week", copilot.text("wait_for_update", timeout_s=5))
        captions(self.port, [("Rahul", "can we close pricing today"), ("You", "maybe next week"), ("Rahul", "fine")])
        self.assertIn("Rahul: fine", copilot.text("wait_for_update", timeout_s=5))
        self.assertEqual(copilot.text("wait_for_update", timeout_s=5), "(no new speech)")

    def test_rebuilt_caption_list_does_not_repeat_speech(self):
        copilot = self.session()
        copilot.text("start")
        report(self.port)
        captions(self.port, [("Rahul", "first point"), ("You", "noted"), ("Rahul", "second point")])
        time.sleep(1.5)
        copilot.text("wait_for_update", timeout_s=5)
        captions(self.port, [("You", "noted"), ("Rahul", "second point"), ("Rahul", "third point")], epoch=1)
        update = copilot.text("wait_for_update", timeout_s=5)
        self.assertIn("Rahul: third point", update)
        self.assertNotIn("second point", update)
        self.assertEqual(copilot.text("get_transcript").count("second point"), 1)

    def test_tells_the_agent_when_the_screen_changes(self):
        copilot = self.session()
        copilot.text("start")
        report(self.port, presenter="Priya")
        self.assertEqual(frame(self.port)[1], {"ok": True, "id": 1})
        update = copilot.text("wait_for_update", timeout_s=5)
        self.assertIn("Priya started presenting", update)
        self.assertIn("[screen] Priya's shared screen changed: frame 1", update)
        image = [part for part in copilot.call("get_screen", frame=1)["content"] if part["type"] == "image"][0]
        self.assertEqual(base64.b64decode(image["data"]), JPEG)

    def test_get_screen_asks_the_tab_for_a_fresh_frame(self):
        copilot = self.session()
        copilot.text("start")
        report(self.port)
        result = {}
        worker = threading.Thread(target=lambda: result.update(copilot.call("get_screen")))
        worker.start()
        deadline = time.time() + 5
        while not report(self.port)[1]["wantFrame"]:
            self.assertLess(time.time(), deadline)
            time.sleep(0.1)
        frame(self.port, reason="requested")
        worker.join(5)
        self.assertEqual([part["type"] for part in result["content"]], ["text", "image"])
        self.assertFalse(report(self.port)[1]["wantFrame"])

    def test_rejects_requests_a_web_page_could_forge(self):
        self.session().text("start")
        self.assertEqual(post(self.port, "/ext/report", TAB, headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(post(self.port, "/ext/report", TAB, bare=True)[0], 403)
        self.assertEqual(post(self.port, "/api/handover", {}, headers={"Origin": "chrome-extension://abc"})[0], 403)
        self.assertEqual(post(self.port, "/ext/report", TAB, headers={"Origin": "chrome-extension://abc"})[0], 200)

    def test_second_session_takes_the_meeting_over(self):
        first = self.session()
        first.text("start")
        report(self.port)
        captions(self.port, [("Rahul", "budget is ten lakh"), ("You", "ok")])
        first.text("wait_for_update", timeout_s=5)
        second = self.session()
        self.assertIn("Took the meeting over", second.text("start"))
        self.assertIn("took over", first.text("wait_for_update", timeout_s=5))
        self.assertIn("Rahul: budget is ten lakh", second.text("get_transcript"))
        report(self.port)
        captions(self.port, [("Rahul", "budget is ten lakh"), ("You", "ok"), ("Rahul", "and a pilot")])
        time.sleep(1.5)
        update = second.text("wait_for_update", timeout_s=5)
        self.assertIn("Rahul: and a pilot", update)
        self.assertNotIn("ten lakh", update)

    def test_stop_saves_the_transcript_and_restart_rebinds(self):
        copilot = self.session()
        copilot.text("start")
        report(self.port)
        captions(self.port, [("Rahul", "we sign on monday")])
        stopped = copilot.text("stop")
        folder = Path(stopped.rsplit(" in ", 1)[1].strip())
        self.assertIn("we sign on monday", (folder / "transcript.md").read_text(encoding="utf-8"))
        with self.assertRaises(urllib.error.URLError):
            report(self.port)
        self.assertIn("Sotto is listening", copilot.text("start"))
        self.assertEqual(report(self.port)[0], 200)


if __name__ == "__main__":
    unittest.main()
