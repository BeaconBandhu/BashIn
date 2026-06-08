// BashIn Bridge — service worker.
// Holds a WebSocket to the local Python overlay (ws://127.0.0.1:8777) and relays
// commands to the content script in the right tab. The SW (chrome-extension://
// origin) is NOT subject to Swiggy's page CSP, so the WS connects reliably.

const WS_URL = "ws://127.0.0.1:8777";
let ws = null;

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  try {
    ws = new WebSocket(WS_URL);
  } catch (e) {
    setTimeout(connect, 2000);
    return;
  }
  ws.onopen = () => {
    try { ws.send(JSON.stringify({ type: "sw_hello" })); } catch (e) {}
  };
  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch (e) { return; }
    if (msg.type === "ping") { safeSend({ type: "pong" }); return; }
    if (msg.id && msg.action) {
      const result = await handleCommand(msg);
      safeSend({ id: msg.id, result });
    }
  };
  ws.onclose = () => { ws = null; setTimeout(connect, 1500); };
  ws.onerror = () => { try { ws.close(); } catch (e) {} };
}

function safeSend(obj) {
  try { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj)); } catch (e) {}
}

async function handleCommand(msg) {
  const tab = await findTab(msg.tabMatch, 25000);
  if (!tab) return { ok: false, reason: "NO_TAB" };
  // Retry sendMessage until the content script answers (it injects at document_idle)
  const t0 = Date.now();
  while (Date.now() - t0 < 25000) {
    try {
      const res = await chrome.tabs.sendMessage(tab.id, {
        action: msg.action, qty: msg.qty
      });
      if (res) return res;
    } catch (e) { /* content script not ready yet */ }
    await sleep(400);
  }
  return { ok: false, reason: "NO_CONTENT" };
}

async function findTab(match, timeout) {
  const t0 = Date.now();
  while (Date.now() - t0 < timeout) {
    const tabs = await chrome.tabs.query({});
    const m = tabs.filter(t => t.url && t.url.includes(match));
    if (m.length) { m.sort((a, b) => b.id - a.id); return m[0]; }  // newest tab
    await sleep(400);
  }
  return null;
}

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

// A content script announcing itself wakes the SW → ensure WS is connected.
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg && msg.type === "ready") connect();
});

// Keepalive: wake every 30s and reconnect if needed.
chrome.alarms.create("keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => connect());

connect();
