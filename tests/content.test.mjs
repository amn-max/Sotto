// Checks chrome-extension/content.js caption handling against stand-ins for Meet's and Teams' caption lists.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../chrome-extension/content.js', import.meta.url), 'utf8');
const MEET_CAPTIONS = 'div[role="region"][aria-label="Captions"]';
const TEAMS_CAPTIONS = '[data-tid="closed-caption-v2-virtual-list-content"]';

function load(url) {
  const { hostname, pathname, href } = new URL(url);
  const sent = [];
  const page = { sent, elements: {} };
  const canvas = () => ({
    getContext: () => ({ drawImage() {}, getImageData: () => ({ data: new Uint8ClampedArray(32 * 18 * 4) }) }),
  });
  page.context = vm.createContext({
    console,
    location: { hostname, pathname, href },
    document: {
      title: 'Pricing call',
      createElement: canvas,
      querySelector: (selector) => page.elements[selector] || null,
      querySelectorAll: () => [],
    },
    chrome: { runtime: { sendMessage: (message, done) => { sent.push(message); done({ ok: true }); } } },
    setInterval: () => 0,
  });
  vm.runInContext(source, page.context);
  page.run = (code) => vm.runInContext(code, page.context);
  return page;
}

// Meet: one row per turn, an avatar, the speaker's name in the first span, then the text.
function meetCaptions(page, rows) {
  page.elements[MEET_CAPTIONS] = {
    children: rows.map(([speaker, text]) => ({
      querySelector: (selector) => (selector === 'img' ? {} : selector === 'span' ? { textContent: speaker } : null),
      textContent: `${speaker} ${text}`,
    })),
  };
}

// Teams: each caption text sits inside a group that also holds the author's name.
function teamsCaptions(page, rows) {
  const texts = rows.map(([speaker, text]) => {
    const group = { querySelector: (selector) => (selector === '[data-tid="author"]' ? { textContent: speaker } : null) };
    return { textContent: text, parentElement: group, querySelector: () => null };
  });
  page.elements[TEAMS_CAPTIONS] = { querySelectorAll: () => texts };
}

const payloads = (page) => page.sent.map((message) => message.payload);

test('numbers each speaker\'s turns', () => {
  const page = load('https://meet.google.com/abc-defg-hij');
  const keys = page.context.keyRows([
    { speaker: 'Rahul', text: 'a' },
    { speaker: 'You', text: 'b' },
    { speaker: 'Rahul', text: 'c' },
  ]).map((r) => r.key);
  assert.deepEqual([...keys], ['Rahul#1', 'You#1', 'Rahul#2']);
});

test('reads the meeting code from Meet and Teams links', () => {
  assert.equal(load('https://meet.google.com/abc-defg-hij').run('PLATFORM.meetingCode()'), 'abc-defg-hij');
  assert.equal(load('https://meet.google.com/_meet/abc-defg-hij').run('PLATFORM.meetingCode()'), 'abc-defg-hij');
  assert.equal(load('https://meet.google.com/landing').run('PLATFORM.meetingCode()'), null);
  assert.equal(load('https://teams.live.com/meet/9876543210123?p=x').run('PLATFORM.meetingCode()'), '9876543210123');
});

test('sends Meet captions only when they change, with the speaker name stripped', () => {
  const page = load('https://meet.google.com/abc-defg-hij');
  page.run('serverUp = true');
  meetCaptions(page, [['Rahul', 'can we close today'], ['You', 'maybe']]);
  page.context.pollCaptions();
  page.context.pollCaptions();
  assert.equal(page.sent.length, 1);
  const [first] = payloads(page);
  assert.equal(first.platform, 'meet');
  assert.equal(first.meeting, 'abc-defg-hij');
  assert.equal(first.bottomKey, 'You#1');
  assert.deepEqual([...first.rows.map((r) => r.text)], ['can we close today', 'maybe']);

  meetCaptions(page, [['Rahul', 'can we close today'], ['You', 'maybe next week']]);
  page.context.pollCaptions();
  assert.equal(payloads(page)[1].rows.at(-1).text, 'maybe next week');
  assert.equal(payloads(page)[1].epoch, first.epoch);
});

test('starts a new epoch when Meet rebuilds the caption list', () => {
  const page = load('https://meet.google.com/abc-defg-hij');
  page.run('serverUp = true');
  meetCaptions(page, [['Rahul', 'one'], ['You', 'two'], ['Rahul', 'three']]);
  page.context.pollCaptions();
  meetCaptions(page, [['Rahul', 'three']]);
  page.context.pollCaptions();
  assert.equal(payloads(page)[1].epoch, payloads(page)[0].epoch + 1);
  assert.equal(payloads(page)[1].bottomKey, 'Rahul#1');
});

test('reads Teams captions with their authors', () => {
  const page = load('https://teams.live.com/meet/9876543210123');
  page.run('serverUp = true');
  teamsCaptions(page, [['Priya Shah', 'Can you share the deck?'], ['Ayush Naik', 'Sharing now.']]);
  page.context.pollCaptions();
  const [first] = payloads(page);
  assert.equal(first.platform, 'teams');
  assert.deepEqual([...first.rows.map((r) => `${r.speaker}: ${r.text}`)], ['Priya Shah: Can you share the deck?', 'Ayush Naik: Sharing now.']);
});

test('starts a new epoch when Teams drops old captions off the top', () => {
  const page = load('https://teams.live.com/meet/9876543210123');
  page.run('serverUp = true');
  teamsCaptions(page, [['Priya', 'one'], ['Ayush', 'two'], ['Priya', 'three']]);
  page.context.pollCaptions();
  teamsCaptions(page, [['Ayush', 'two'], ['Priya', 'three'], ['Priya', 'four']]);
  page.context.pollCaptions();
  assert.equal(payloads(page)[1].epoch, payloads(page)[0].epoch + 1);
});

test('sends nothing while no copilot is listening', () => {
  const page = load('https://meet.google.com/abc-defg-hij');
  meetCaptions(page, [['Rahul', 'hello']]);
  page.context.pollCaptions();
  assert.equal(page.sent.length, 0);
});
