# Sotto

> *sotto voce* — in a quiet voice, so only the person beside you hears.

**A silent copilot for your Google Meet calls, inside the AI agent you already use.**

![Claude Code](https://img.shields.io/badge/Claude%20Code-plugin-D97757)
![Codex](https://img.shields.io/badge/Codex-plugin-10A37F)
![Gemini CLI](https://img.shields.io/badge/Gemini%20CLI-extension-4285F4)
![Cost](https://img.shields.io/badge/extra%20cost-none-2E7D32)

Sotto follows your meeting as it happens: what people say, and whatever they put on screen. While the conversation is still going, your agent sends you short private cues: the question worth asking now, the objection that's coming, the fact from your own work that settles the point.

Nothing joins the call, and nothing is said or posted into it. The only cost is your agent's subscription.

---

## What you see

Cues appear in your agent's chat while people talk:

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

- **It knows your work.** Sotto runs inside your AI agent, so mid-meeting it can look through the projects, documents and notes your agent can see. Typical meeting assistants only know the meeting.
- **Nothing joins the call.** There's no bot in the participant list and nothing in the chat.
- **It sees the screen.** When someone presents, your agent can read the slide, dashboard or document they're showing.
- **Free and light.** It uses Google Meet's own captions, so there's no transcription service, no API key and no account to create. It barely uses your computer.
- **Any caption language.** It works in every language Google Meet can caption.

## Works with

| Agent | Start Sotto with | |
|---|---|---|
| **Claude Code** | `/sotto` | Tested in live meetings |
| **Codex CLI** | `$sotto` | Supported |
| **Gemini CLI** | `/sotto` | Supported |

You need **Google Chrome** or **Microsoft Edge** for the meeting, with Google Meet's interface in English.

## Install

Setup takes about five minutes, and you only do it once.

### 1. Install uv

Sotto's background helper runs through [uv](https://docs.astral.sh/uv/), a small free tool that also sets up Python if you don't have it.

**Windows** (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**macOS or Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 2. Add the browser extension

1. **[Download the Sotto extension](https://github.com/amn-max/Sotto/releases/latest/download/sotto-chrome-extension.zip)** (always the newest version).
2. Unzip it into a folder you'll keep, such as `Documents\Sotto extension`. The browser loads Sotto from that folder, so don't delete it.
3. Open `chrome://extensions` (or `edge://extensions`), switch on **Developer mode**, click **Load unpacked**, and choose the folder you unzipped.

Do this in the browser profile you join meetings from. Sotto isn't in the Chrome Web Store yet, which is why it installs through developer mode.

### 3. Add Sotto to your agent

<details open>
<summary><b>Claude Code</b></summary>

In a terminal:

```bash
claude plugin marketplace add amn-max/Sotto
claude plugin install sotto@sotto
```

Or inside Claude Code: `/plugin marketplace add amn-max/Sotto`, then `/plugin install sotto@sotto`.
</details>

<details>
<summary><b>Codex CLI</b></summary>

```bash
codex plugin marketplace add amn-max/Sotto
codex plugin add sotto@sotto
```
</details>

<details>
<summary><b>Gemini CLI</b></summary>

```bash
gemini extensions install https://github.com/amn-max/Sotto
```

The first time Gemini asks to run a Sotto tool, choose to always allow it, so it can keep listening without stopping to ask.

With a personal Google account, Gemini CLI signs in with a Gemini API key, which is free from [Google AI Studio](https://aistudio.google.com/apikey).
</details>

Then start a new session in your agent.

## Use it

1. **Join your meeting** in the browser and turn on captions: press `c`, or click the **CC** button. Only you see them.
2. **Start Sotto** in your agent, and say what the meeting is about and what you want from it:

   ```
   /sotto Vendor call on telephony pricing. I want under 40 paise a minute and a 30-day pilot.
   ```

   In Codex, type `$sotto` instead of `/sotto`.

   The description is optional; with just `/sotto`, it works out the context as the meeting goes. The more you tell it (your goal, who's on the other side, what to watch for), the sharper the cues.

3. **Ask it anything along the way.** Type into the same chat: "what should I ask about SLAs?", "is that number right?", "summarise the last five minutes". It answers, then goes back to listening.
4. **Finish.** Say "stop", or just leave the call. Sotto writes a wrap-up of decisions, action items with owners, and open questions.

**Tips**

- Keep your agent on a second screen, or beside the meeting window.
- When you share your screen, share a tab or a window rather than the whole screen, so your cues stay private.
- A faster model makes cues arrive sooner.

## Languages

Sotto works in any language Google Meet can caption ([see Google's list](https://support.google.com/meet/answer/15077804)). Meet captions one language at a time, so set it to the language the meeting is in:

1. In the meeting, open **⋮ More options → Settings → Captions**.
2. Under **Language of the meeting**, choose the language.

Your agent writes its cues in the language you write to it in, whatever language the captions are in.

## Updating

- **Browser extension:** [download the newest zip](https://github.com/amn-max/Sotto/releases/latest/download/sotto-chrome-extension.zip), unzip it over your existing Sotto folder, then click the reload button on Sotto's card in `chrome://extensions`.
- **Claude Code:** `claude plugin marketplace update sotto`, then `claude plugin update sotto@sotto`.
- **Codex CLI:** `codex plugin marketplace upgrade`, then `codex plugin add sotto@sotto`.
- **Gemini CLI:** `gemini extensions update sotto`.

## Troubleshooting

| What you see | What to do |
|---|---|
| "Meet tab not connected" | Check that Sotto is switched on in `chrome://extensions`, then refresh the meeting tab. |
| "Captions are OFF" | Press `c` in the meeting, or click the **CC** button. |
| Cues don't match what's being said | Check the caption language (see [Languages](#languages)). |
| `/sotto` isn't recognised | Start a new session in your agent after installing. |
| "Port 47823 is held by another program" | Another app is using Sotto's port. Close it, or see [CONTRIBUTING.md](CONTRIBUTING.md#settings) to change the port. |

## Privacy

- **Stays on your computer:** the meeting transcript and screen snapshots, saved in a `Sotto` folder in your home folder. Delete them whenever you like.
- **Goes to your agent's provider:** the caption text, and any snapshots your agent looks at, go to the company behind your agent (Anthropic, OpenAI or Google) as part of your normal session with them.
- **Goes nowhere else:** there's no Sotto server, no account and no tracking, and nothing is ever posted to the meeting.
- **Other people in the meeting** aren't notified. Whether and how to tell them is up to you. Follow your organisation's rules, especially for calls with customers.

## Limits

- **Captions aren't perfect.** Names, numbers, and speech in a language other than the caption language can come through garbled. Your agent reads past a lot of it from context, but it can't hear the audio.
- **Screen sharing** works when someone else presents; your own shared screen isn't captured.
- **Google Meet only,** in Chrome or Edge, with Meet's interface in English.

---

Building on Sotto or changing it? See [CONTRIBUTING.md](CONTRIBUTING.md).
