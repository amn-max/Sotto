// Sotto: reads your own meeting tab (captions, who is presenting, the shared screen) and reports it
// to the Sotto server on this computer. Works in Google Meet and Microsoft Teams on the web.

const SID = Math.random().toString(36).slice(2, 10);
const CAPTION_POLL_MS = 1000;
const REPORT_MS = 2000;
const REPORT_EVERY_WHEN_DOWN = 5; // while no copilot is listening, report every 5th tick
const ROWS_PER_UPDATE = 8;
const ROWS_ON_CONNECT = 60; // a copilot that starts mid-meeting catches up on recent speech
const FRAME_MAX_WIDTH = 1568;
const FRAME_MIN_GAP_MS = 8000;

let serverUp = false;
let epoch = 0;
let lastRowCount = 0;
let lastFirstRow = '';
let lastSent = '';
let catchUp = true;
let ticks = 0;

const clean = (value) => (value || '').replace(/\s+/g, ' ').trim();

// Platforms. Each one knows how its meeting page shows captions, the call, and screen sharing.

const MEET = {
  id: 'meet',
  meetingCode() {
    const match = location.pathname.match(/(?:^|\/)([a-z]{3,}-[a-z]{3,}-[a-z]{3,})(?=$|\/)/i);
    return match ? match[1].toLowerCase() : null;
  },
  // Each caption row is one speaker turn: an avatar, the speaker's name in the first span, then the text.
  captionRows() {
    const region = document.querySelector('div[role="region"][aria-label="Captions"]');
    if (!region) return null;
    const rows = [];
    for (const child of region.children) {
      if (!child.querySelector('img')) continue;
      const speaker = clean(child.querySelector('span')?.textContent) || 'Unknown';
      let text = clean(child.textContent);
      if (text.startsWith(speaker)) text = text.slice(speaker.length).trim();
      rows.push({ speaker, text });
    }
    return rows;
  },
  // Newer Meet has one pressable captions button (found by its icon, whatever the UI language);
  // older Meet labels the button by the action it would take.
  captionsState() {
    const icons = ['translate_cc', 'closed_caption', 'closed_caption_off'];
    const isCaptionsButton = (button) =>
      icons.includes(clean(button.querySelector('i')?.textContent)) ||
      button.getAttribute('aria-label') === 'Captions and translation';
    const pressable = [...document.querySelectorAll('button[aria-pressed]')].find(isCaptionsButton);
    if (pressable) return pressable.getAttribute('aria-pressed') === 'true' ? 'on' : 'off';
    if (document.querySelector('[aria-label="Turn off captions" i]')) return 'on';
    if (document.querySelector('[aria-label="Turn on captions" i]')) return 'off';
    return 'unknown';
  },
  inCall() {
    return !!document.querySelector('[aria-label="Leave call"], [data-tooltip="Leave call"]');
  },
  // The present button's label says who is presenting: "<name> is presenting" or "You are presenting".
  presenter() {
    for (const el of document.querySelectorAll('button[aria-label], [role="button"][aria-label], [data-tooltip]')) {
      const label = clean(el.getAttribute('aria-label') || el.getAttribute('data-tooltip'));
      if (/^You are presenting$/i.test(label)) return 'You';
      const match = label.match(/^(.+?)\s+is presenting$/i);
      if (match) return match[1];
    }
    return null;
  },
  screenVideo() {
    return largestVideo(document);
  },
};

const TEAMS = {
  id: 'teams',
  meetingCode() {
    const match = location.href.match(/\/meet\/(\d{6,20})/) || location.href.match(/meetup-join\/19(?:%3a|:)meeting_([^%@/]+)/i);
    return match ? match[1].toLowerCase() : null;
  },
  // Teams renders only the newest captions; each text sits near its author's name.
  captionRows() {
    const list = document.querySelector('[data-tid="closed-caption-v2-virtual-list-content"]');
    if (!list) return null;
    const rows = [];
    for (const textEl of list.querySelectorAll('[data-tid="closed-caption-text"]')) {
      let group = textEl;
      for (let i = 0; group && i < 4 && !group.querySelector('[data-tid="author"]'); i++) group = group.parentElement;
      const speaker = clean(group?.querySelector('[data-tid="author"]')?.textContent) || 'Unknown';
      rows.push({ speaker, text: clean(textEl.textContent) });
    }
    return rows;
  },
  captionsState() {
    if (document.querySelector('[data-tid="closed-caption-v2-window-wrapper"]')) return 'on';
    return this.inCall() ? 'off' : 'unknown';
  },
  inCall() {
    return !!document.querySelector('#hangup-button');
  },
  // Your own share flips the share button to "Stop sharing"; anyone else's share is a stream named after them.
  presenter() {
    const share = document.querySelector('#screenshare-button');
    if (clean(share?.getAttribute('aria-label')).toLowerCase() === 'stop sharing') return 'You';
    const stream = document.querySelector('[data-stream-type="ScreenSharing"]');
    return stream ? clean(stream.getAttribute('data-tid')) || 'Someone' : null;
  },
  screenVideo() {
    const stream = document.querySelector('[data-stream-type="ScreenSharing"]');
    return (stream && largestVideo(stream)) || largestVideo(document);
  },
};

const PLATFORM = location.hostname.endsWith('meet.google.com') ? MEET : TEAMS;

function send(type, payload) {
  return new Promise((resolve) => {
    try {
      const message = {
        type,
        payload: { sid: SID, platform: PLATFORM.id, meeting: PLATFORM.meetingCode(), title: document.title, ...payload },
      };
      chrome.runtime.sendMessage(message, (reply) => {
        void chrome.runtime.lastError;
        resolve(reply || { ok: false });
      });
    } catch {
      resolve({ ok: false }); // the extension was reloaded and this copy of the script is orphaned
    }
  });
}

// Captions

// A row is identified by its speaker and how many turns that speaker has had so far in the rows on
// screen. That survives Meet swapping row elements on re-render, because older rows never change.
function keyRows(rows) {
  const turns = new Map();
  return rows.map((row) => {
    const n = (turns.get(row.speaker) || 0) + 1;
    turns.set(row.speaker, n);
    return { key: `${row.speaker}#${n}`, speaker: row.speaker, text: row.text };
  });
}

function pollCaptions() {
  if (!serverUp) return;
  const found = PLATFORM.captionRows();
  if (!found) return;
  const rows = keyRows(found);
  // When rows leave the top of the list (Teams keeps only the newest, Meet rebuilds after hiccups),
  // the counts shift. A new epoch gives fresh keys, and the server matches them to turns it already has.
  const firstRow = rows.length ? `${rows[0].speaker}\u0000${rows[0].text.slice(0, 40)}` : '';
  const firstRowLeft = rows.length > 1 && lastRowCount > 1 && firstRow !== lastFirstRow;
  if (rows.length < lastRowCount || firstRowLeft) epoch += 1;
  lastRowCount = rows.length;
  lastFirstRow = firstRow;
  if (!rows.length) return;
  const recent = rows.slice(-(catchUp ? ROWS_ON_CONNECT : ROWS_PER_UPDATE)).filter((row) => row.text);
  const update = { epoch, bottomKey: rows[rows.length - 1].key, rows: recent };
  const fingerprint = JSON.stringify(update);
  if (fingerprint === lastSent) return;
  lastSent = fingerprint;
  catchUp = false;
  send('captions', update);
}

// Shared screen. A 32x18 thumbnail every report is all the checking costs; a full snapshot is
// encoded only when someone else presents and the screen has changed and then held still.

const probe = document.createElement('canvas');
probe.width = 32;
probe.height = 18;
const probeContext = probe.getContext('2d', { willReadFrequently: true });
let lastProbe = null;
let sentProbe = null;
let lastFrameAt = 0;
let stillFor = 0;

function largestVideo(root) {
  let best = null;
  let bestArea = 0;
  for (const video of root.querySelectorAll('video')) {
    if (video.readyState < 2 || !video.videoWidth) continue;
    const box = video.getBoundingClientRect();
    const area = box.width * box.height;
    if (box.width >= 200 && area > bestArea) {
      best = video;
      bestArea = area;
    }
  }
  return best;
}

function thumbnail(video) {
  probeContext.drawImage(video, 0, 0, 32, 18);
  const pixels = probeContext.getImageData(0, 0, 32, 18).data;
  const grey = new Uint8Array(32 * 18);
  for (let i = 0; i < grey.length; i++) {
    grey[i] = (pixels[i * 4] * 3 + pixels[i * 4 + 1] * 6 + pixels[i * 4 + 2]) / 10;
  }
  return grey;
}

function difference(a, b) {
  let sum = 0;
  for (let i = 0; i < a.length; i++) sum += Math.abs(a[i] - b[i]);
  return sum / a.length;
}

function snapshot(video) {
  const scale = Math.min(1, FRAME_MAX_WIDTH / video.videoWidth);
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);
  canvas.getContext('2d').drawImage(video, 0, 0, canvas.width, canvas.height);
  return { jpeg: canvas.toDataURL('image/jpeg', 0.75).split(',')[1], w: canvas.width, h: canvas.height };
}

async function report() {
  ticks += 1;
  if (!serverUp && (ticks - 1) % REPORT_EVERY_WHEN_DOWN !== 0) return;
  const who = PLATFORM.presenter();
  const reply = await send('report', {
    captions: PLATFORM.captionsState(),
    inCall: PLATFORM.inCall(),
    presenter: who,
    version: chrome.runtime.getManifest().version,
  });
  const wasUp = serverUp;
  serverUp = !!reply.ok;
  if (serverUp && !wasUp) {
    // Fresh keys after any gap: the server lines re-sent rows up with the turns it already has.
    epoch += 1;
    lastSent = '';
    catchUp = true;
  }
  if (!serverUp) return;
  const video = PLATFORM.screenVideo();
  if (!video) {
    if (reply.wantFrame) send('frame', { none: true });
    return;
  }
  try {
    const grey = thumbnail(video);
    stillFor = lastProbe && difference(grey, lastProbe) < 1.5 ? stillFor + 1 : 0;
    lastProbe = grey;
    const someoneElse = who && who !== 'You';
    const changed = !sentProbe || difference(grey, sentProbe) > 6;
    const due = someoneElse && stillFor >= 1 && changed && Date.now() - lastFrameAt > FRAME_MIN_GAP_MS;
    if (reply.wantFrame || due) {
      send('frame', { reason: reply.wantFrame ? 'requested' : 'changed', ...snapshot(video) });
      sentProbe = grey;
      lastFrameAt = Date.now();
    }
  } catch (error) {
    if (reply.wantFrame) send('frame', { none: true });
    console.debug('[sotto] screen snapshot skipped:', error);
  }
}

setInterval(pollCaptions, CAPTION_POLL_MS);
setInterval(report, REPORT_MS);
