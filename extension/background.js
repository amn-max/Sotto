// Relays reports from the Meet tab to the Sotto server on this computer. The page itself
// can't call 127.0.0.1 (Meet's CSP and Chrome's local-network rules); the extension can.

const SERVER = 'http://127.0.0.1:47823';
const ROUTES = new Set(['captions', 'report', 'frame']);

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (!ROUTES.has(message?.type)) return false;
  fetch(`${SERVER}/ext/${message.type}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Sotto': '1' },
    body: JSON.stringify(message.payload || {}),
  })
    .then((response) => (response.ok ? response.json() : { ok: false }))
    .catch(() => ({ ok: false }))
    .then(reply);
  return true;
});
