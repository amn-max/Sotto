// Sotto: reads your own Google Meet tab (captions, who is presenting, the shared screen) and
// reports it to the Sotto server on this computer.

const SID = Math.random().toString(36).slice(2, 10);
const CAPTION_POLL_MS = 1000;
const REPORT_MS = 2000;
const REPORT_EVERY_WHEN_DOWN = 5; // while no copilot is listening, report every 5th tick
const ROWS_PER_UPDATE = 8;
const ROWS_ON_CONNECT = 60; // a copilot that starts mid-meeting catches up on recent speech
const FRAME_MAX_WIDTH = 1568;
const FRAME_MIN_GAP_MS = 8000;
const CC_ICONS = ['translate_cc', 'closed_caption', 'closed_caption_off'];

let serverUp = false;
let epoch = 0;
let lastRowCount = 0;
let lastSent = '';
let catchUp = true;
let ticks = 0;

function meetingCode() {
  const match = location.pathname.match(/(?:^|\/)([a-z]{3,}-[a-z]{3,}-[a-z]{3,})(?=$|\/)/i);
  return match ? match[1].toLowerCase() : null;
}

function send(type, payload) {
  return new Promise((resolve) => {
    try {
      const message = { type, payload: { sid: SID, meeting: meetingCode(), title: document.title, ...payload } };
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

function captionRegion() {
  return document.querySelector('div[role="region"][aria-label="Captions"]');
}

// Each caption row is one speaker turn: an avatar, the speaker's name in the first span, then the text.
function readRows(region) {
  const rows = [];
  for (const child of region.children) {
    if (!child.querySelector('img')) continue;
    const speaker = (child.querySelector('span')?.textContent || '').trim() || 'Unknown';
    let text = (child.textContent || '').replace(/\s+/g, ' ').trim();
    if (text.startsWith(speaker)) text = text.slice(speaker.length).trim();
    rows.push({ speaker, text });
  }
  return rows;
}

// Meet swaps row elements on re-render, so a row is identified by its speaker and how many turns
// that speaker has had so far; a re-render can't change that, and Meet never revises older rows.
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
  const region = captionRegion();
  if (!region) return;
  const rows = keyRows(readRows(region));
  if (rows.length < lastRowCount) epoch += 1; // Meet rebuilt the list, so the counts restart
  lastRowCount = rows.length;
  if (!rows.length) return;
  const recent = rows.slice(-(catchUp ? ROWS_ON_CONNECT : ROWS_PER_UPDATE)).filter((row) => row.text);
  const update = { epoch, bottomKey: rows[rows.length - 1].key, rows: recent };
  const fingerprint = JSON.stringify(update);
  if (fingerprint === lastSent) return;
  lastSent = fingerprint;
  catchUp = false;
  send('captions', update);
}

// Call state

// Newer Meet has one pressable captions button (found by its icon, whatever the UI language);
// older Meet labels the button by the action it would take.
function captionsState() {
  const isCaptionsButton = (button) =>
    CC_ICONS.includes((button.querySelector('i')?.textContent || '').trim()) ||
    button.getAttribute('aria-label') === 'Captions and translation';
  const pressable = [...document.querySelectorAll('button[aria-pressed]')].find(isCaptionsButton);
  if (pressable) return pressable.getAttribute('aria-pressed') === 'true' ? 'on' : 'off';
  if (document.querySelector('[aria-label="Turn off captions" i]')) return 'on';
  if (document.querySelector('[aria-label="Turn on captions" i]')) return 'off';
  return 'unknown';
}

function inCall() {
  return !!document.querySelector('[aria-label="Leave call"], [data-tooltip="Leave call"]');
}

// The present button's label says who is presenting: "<name> is presenting" or "You are presenting".
function presenter() {
  for (const el of document.querySelectorAll('button[aria-label], [role="button"][aria-label], [data-tooltip]')) {
    const label = (el.getAttribute('aria-label') || el.getAttribute('data-tooltip') || '').trim();
    if (/^You are presenting$/i.test(label)) return 'You';
    const match = label.match(/^(.+?)\s+is presenting$/i);
    if (match) return match[1];
  }
  return null;
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

function largestVideo() {
  let best = null;
  let bestArea = 0;
  for (const video of document.querySelectorAll('video')) {
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
  const who = presenter();
  const reply = await send('report', {
    captions: captionsState(),
    inCall: inCall(),
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
  const video = largestVideo();
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
