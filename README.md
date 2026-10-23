# Sotto

> *sotto voce* — in a quiet voice, so only the person beside you hears.

**A silent copilot for Google Meet, inside Claude Code.**

![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-D97757)
![MCP server](https://img.shields.io/badge/MCP-stdio-4B5563)
![Dependencies](https://img.shields.io/badge/dependencies-none-2E7D32)
![Cost](https://img.shields.io/badge/extra%20cost-none-2E7D32)

Sotto reads your own Meet tab (the live captions and whatever is being presented) and passes it to Claude. While the meeting is still going, Claude sends you short private cues: the question worth asking now, the objection that's coming, the number from your own work that settles the point.

Nothing joins the call, and nothing is said or posted into it. The only cost is your Claude subscription.

---

## What you see

The cues appear in your Claude Code panel while people talk:

```
— Rahul: "we can go live on the 15th, assuming your side has the webhook ready"

  Ask:   Which webhook — booking-created only, or reschedules and cancels too?
  Flag:  "Assuming your side" puts the date's risk on you. Get his dependency list in writing.

— Priya starts presenting the pricing deck

  Context: The slide says 38 paise/min; last week's quote in your notes was 34.

— You type: "what did we agree on SLAs last time?"

  Answer: 99.5% monthly uptime, with credits starting below 99%. It's in the vendor-eval doc.
```

Sotto stays quiet unless a cue would change what you say or do in the next minute. It doesn't narrate the meeting back to you.

## Why Sotto

- **It knows your work.** Sotto runs inside Claude Code, so mid-meeting it can check your repositories, documents, and notes. Typical meeting assistants only know the meeting.
- **Nothing joins the call.** There's no bot participant and nothing in the chat. It reads your own tab, the way you do.
- **It sees the screen.** When someone presents, Sotto snapshots the shared screen each time it changes, so Claude can read the slide, the dashboard, or the doc.
- **Free.** It uses Meet's own captions: no transcription service, no API keys, no accounts.
- **Light.** The extension reads caption text once a second and compares a 32×18 thumbnail of the screen every two seconds. The server is a single standard-library Python file that idles at about zero CPU.
- **Any caption language.** Sotto works in every language Google Meet can caption. See [Languages](#languages).

## How it works

```mermaid
flowchart LR
  subgraph tab["Your Meet tab (Chrome or Edge)"]
    captions["Meet's live captions"]
    screen["Shared screen"]
  end
  extension["Sotto extension"]
  server["Sotto server<br/>127.0.0.1:47823"]
  claude["Claude Code<br/>/sotto:listen"]
  you(("You"))
  captions --> extension
  screen --> extension
  extension -- "captions · status · snapshots" --> server
  server -- "MCP tools" --> claude
  claude -- "cues" --> you
```

| Piece | What it does |
|---|---|
| **Extension** (`extension/`) | Reads Meet's caption list and who is presenting, then posts them to the local server. Encodes a full screen snapshot only when someone else is presenting and the screen has changed and settled. |
| **Server** (`server/server.py`) | An MCP server for Claude Code and a loopback endpoint for the extension, in one process with no dependencies. It opens its port only while you're listening. If another Claude Code session starts listening, that session takes the meeting over, transcript included. |
| **Skill** (`skills/listen/`) | Tells Claude how to run the meeting loop, when a cue is worth writing, and how to keep cues short. |

Each speaker turn reaches Claude once the speaker pauses, usually within a couple of seconds. During long monologues, Claude gets updates as the speaker goes.

## Quick start

**You need:** Google Chrome or Microsoft Edge, [Claude Code](https://claude.com/claude-code), and [uv](https://docs.astral.sh/uv/). uv finds or fetches Python 3.9 or newer; no packages are installed. Meet's interface must be in English, because the extension finds the captions by their labels.

**1. Get the code**

```bash
git clone <repo-url> sotto
```

**2. Load the extension** (once, in the browser profile you join meetings from)

Open `chrome://extensions`, switch on **Developer mode**, click **Load unpacked**, and choose the `sotto/extension` folder.

**3. Install the plugin**

```bash
claude plugin marketplace add ./sotto
claude plugin install sotto@sotto
```

Then open a new Claude Code session; the server loads when a session starts. Inside Claude Code, `/plugin` does the same job.

## Using Sotto

1. **Join the meeting** in Chrome and turn captions on (press `c`). Only you see them.
2. **Start listening** in Claude Code and tell it what the meeting is for:

   ```
   /sotto:listen Vendor call on telephony pricing. I want under 40 paise a minute and a 30-day pilot.
   ```

   The brief is optional. On its own, `/sotto:listen` works out the context from the conversation. A good brief says what you want out of the meeting, who's on the other side, and anything to watch for.

3. **Talk to it any time.** Type into the same session: "what should I ask about SLAs?", "is that number right?", "summarise the last five minutes". It answers, then goes back to listening.
4. **Finish.** Say "stop", or just leave the call. Sotto writes a wrap-up: decisions, action items with owners, and open questions.

**Tips**

- Keep Claude Code on a second screen, next to the meeting.
- When you present, share a tab or a window rather than your whole screen.
- For faster cues, switch to a quicker model with `/model`, or turn on `/fast`.

## Languages

Sotto works in any language Google Meet can caption ([see Google's list](https://support.google.com/meet/answer/15077804)). Meet captions one language at a time, so set it to the language the meeting is in:

1. In the meeting, open **⋮ More options → Settings → Captions**.
2. Under **Language of the meeting**, choose the language.

Claude writes its cues in the language you write to it in, whatever the caption language is.

## Privacy

- **On your computer:** the transcript and screen snapshots, saved to `~/Sotto/meetings/<date>_<meeting-code>/`. Delete them whenever you like.
- **Sent to Anthropic:** the caption text, and any snapshots Claude looks at, go to Anthropic as part of your Claude Code session, under your existing Claude terms.
- **Sent anywhere else:** nothing. There's no other service and no telemetry, and nothing is posted to the meeting.
- **Other participants:** they aren't notified. Whether and how to tell them is up to you. Follow your organisation's rules, especially for calls with customers.

The local endpoint only answers requests carrying Sotto's own header, and refuses anything sent by a web page, so other sites you have open can't push data into it.

## Tools

Sotto gives Claude five MCP tools:

| Tool | Purpose |
|---|---|
| `start` | Opens the local endpoint and reports what's connected: the Meet tab, captions on or off, who is presenting. Safe to call again as a status check. |
| `wait_for_update` | The listening loop. Waits until there's new speech, a screen change, or a status change, then returns it. |
| `get_screen` | Returns a saved snapshot, or asks the tab for a fresh one. |
| `get_transcript` | Everything said so far, or the last N minutes. |
| `stop` | Stops listening and returns where the transcript and snapshots were saved. |

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SOTTO_PORT` | `47823` | Local port. If you change it, change it in `extension/manifest.json` and `extension/background.js` too. |
| `SOTTO_DIR` | `~/Sotto` | Where meetings are saved. |

## Other MCP clients

The server is a plain stdio MCP server, so any MCP client can use it. In Codex CLI, add this to `~/.codex/config.toml`:

```toml
[mcp_servers.sotto]
command = "uv"
args = ["run", "--quiet", "--no-project", "--directory", "/path/to/sotto/server", "server.py"]
```

The listening behaviour itself lives in `skills/listen/SKILL.md`; give your agent those instructions too.

## Limits

- **Caption accuracy.** Names, numbers, and speech in the other language come through garbled. Claude reads past a lot of it from context, but it can't hear the audio.
- **Screen snapshots** come from the biggest video in the tab while someone else presents. Your own shared screen isn't captured.
- **Google Meet only,** in Chrome or Edge, with Meet's interface in English.

## Development

```
.claude-plugin/   plugin manifest and local marketplace
extension/        Chrome extension (Manifest V3)
server/server.py  MCP server and loopback endpoint, standard library only
skills/listen/    the /sotto:listen skill
tests/            server end-to-end tests and extension logic tests
```

```bash
python -m unittest discover -s tests     # server, driven over MCP stdio and HTTP
node --test tests/content.test.mjs       # extension caption handling
claude plugin validate .                 # plugin manifest
```

After editing the extension, reload it in `chrome://extensions` and refresh the Meet tab. After editing the server or the skill, start a new Claude Code session.
