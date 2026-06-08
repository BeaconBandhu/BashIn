// BashIn Bridge — service worker.
// Holds a WebSocket to the local overlay (ws://127.0.0.1:8777). On a command it
// OPENS the target page itself (chrome.tabs.create, in its own profile), waits
// for it to load, then tells the content script what to do. Opening the tab here
// guarantees the content script is present and we target the exact tab.

const WS_URL = "ws://127.0.0.1:8777";
let ws = null;

function connect() {
  if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
  try { ws = new WebSocket(WS_URL); }
  catch (e) { setTimeout(connect, 2000); return; }

  ws.onopen = () => safeSend({ type: "sw_hello" });
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

const sleep = (ms) => new Promise(r => setTimeout(r, ms));

async function handleCommand(msg) {
  let tab;
  try {
    tab = await chrome.tabs.create({ url: msg.url, active: true });
  } catch (e) {
    return { ok: false, reason: "TAB_CREATE_FAILED", error: String(e) };
  }

  await waitForLoad(tab.id, 30000);

  // Retry until the content script answers (it injects at document_idle)
  const t0 = Date.now();
  while (Date.now() - t0 < 30000) {
    try {
      const res = await chrome.tabs.sendMessage(tab.id, { action: msg.action, qty: msg.qty });
      if (res) return res;
    } catch (e) { /* content script not ready yet */ }
    await sleep(400);
  }
  return { ok: false, reason: "NO_CONTENT" };
}

function waitForLoad(tabId, timeout) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const check = async () => {
      try {
        const t = await chrome.tabs.get(tabId);
        if (t.status === "complete") return resolve(true);
      } catch (e) { return resolve(false); }
      if (Date.now() - t0 > timeout) return resolve(false);
      setTimeout(check, 300);
    };
    check();
  });
}

// Keepalive + reconnect.
chrome.alarms.create("keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(() => connect());
chrome.runtime.onMessage.addListener(() => connect());  // content scripts wake us

connect();
