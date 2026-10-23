// Checks chrome-extension/content.js caption handling against a stand-in for Meet's caption list.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../chrome-extension/content.js', import.meta.url), 'utf8');

function load() {
  const sent = [];
  const canvas = () => ({
    getContext: () => ({ drawImage() {}, getImageData: () => ({ data: new Uint8ClampedArray(32 * 18 * 4) }) }),
  });
  const context = vm.createContext({
    console,
    location: { pathname: '/abc-defg-hij' },
    document: { title: 'Pricing call', createElement: canvas, querySelector: () => null, querySelectorAll: () => [] },
    chrome: { runtime: { sendMessage: (message, done) => { sent.push(message); done({ ok: true }); } } },
    setInterval: () => 0,
  });
  vm.runInContext(source, context);
  return { context, sent, run: (code) => vm.runInContext(code, context) };
}

const row = (speaker, text) => ({
  querySelector: (selector) => (selector === 'img' ? {} : selector === 'span' ? { textContent: speaker } : null),
  textContent: `${speaker} ${text}`,
});

function withCaptions(page, rows) {
  page.context.captionRegion = () => ({ children: rows.map(([speaker, text]) => row(speaker, text)) });
}

test('numbers each speaker\'s turns', () => {
  const { context } = load();
  const keys = context.keyRows([
    { speaker: 'Rahul', text: 'a' },
    { speaker: 'You', text: 'b' },
    { speaker: 'Rahul', text: 'c' },
  ]).map((r) => r.key);
  assert.deepEqual([...keys], ['Rahul#1', 'You#1', 'Rahul#2']);
});

test('reads the meeting code from the URL', () => {
  const { context } = load();
  assert.equal(context.meetingCode(), 'abc-defg-hij');
  context.location.pathname = '/_meet/abc-defg-hij';
  assert.equal(context.meetingCode(), 'abc-defg-hij');
  context.location.pathname = '/landing';
  assert.equal(context.meetingCode(), null);
});

test('sends captions only when they change, with the speaker name stripped', () => {
  const page = load();
  page.run('serverUp = true');
  withCaptions(page, [['Rahul', 'can we close today'], ['You', 'maybe']]);
  page.context.pollCaptions();
  page.context.pollCaptions();
  assert.equal(page.sent.length, 1);
  const first = page.sent[0].payload;
  assert.equal(first.meeting, 'abc-defg-hij');
  assert.equal(first.bottomKey, 'You#1');
  assert.deepEqual([...first.rows.map((r) => r.text)], ['can we close today', 'maybe']);

  withCaptions(page, [['Rahul', 'can we close today'], ['You', 'maybe next week']]);
  page.context.pollCaptions();
  assert.equal(page.sent.length, 2);
  assert.equal(page.sent[1].payload.rows.at(-1).text, 'maybe next week');
});

test('starts a new epoch when Meet rebuilds the caption list', () => {
  const page = load();
  page.run('serverUp = true');
  withCaptions(page, [['Rahul', 'one'], ['You', 'two'], ['Rahul', 'three']]);
  page.context.pollCaptions();
  withCaptions(page, [['Rahul', 'three']]);
  page.context.pollCaptions();
  assert.equal(page.sent[0].payload.epoch, 0);
  assert.equal(page.sent[1].payload.epoch, 1);
  assert.equal(page.sent[1].payload.bottomKey, 'Rahul#1');
});

test('sends nothing while no copilot is listening', () => {
  const page = load();
  withCaptions(page, [['Rahul', 'hello']]);
  page.context.pollCaptions();
  assert.equal(page.sent.length, 0);
});
