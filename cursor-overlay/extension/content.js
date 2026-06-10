// BashIn Bridge — content script.
// Runs inside Swiggy / Google Calendar pages in the user's real Chrome.
// Receives actions from the service worker and performs real DOM clicks.

(() => {
  const ADD      = '[data-testid="buttonpair-add"]';
  const CARTBAR  = '[data-testid="veiwcartbar-container"]';
  const OVERLAY  = '[aria-label^="Please select variant"], [aria-label="Items list"]';
  const VAR_ADD  = '[aria-label="Add 1 item to cart"]';
  const CLOSE_OV = '[aria-label="Close overlay"]';

  const $   = (s) => document.querySelector(s);
  const $$  = (s) => Array.from(document.querySelectorAll(s));
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  async function waitFor(sel, timeout = 15000) {
    const t0 = Date.now();
    while (Date.now() - t0 < timeout) {
      if (document.querySelector(sel)) return true;
      await sleep(300);
    }
    return false;
  }

  // Find a visible, leaf-ish clickable element whose text matches `re`.
  function findClickable(re) {
    const els = $$('button, [role="button"], a, div, span');
    return els.find(e => {
      const t = (e.textContent || "").trim();
      return re.test(t) && t.length < 45 && e.offsetParent !== null;
    });
  }

  // Snapshot of visible button/CTA text — returned on failure for debugging.
  function visibleButtons() {
    return $$('button, [role="button"], a')
      .filter(e => e.offsetParent !== null)
      .map(e => (e.textContent || "").trim())
      .filter(t => t && t.length < 45)
      .slice(0, 30);
  }

  // In the variant overlay, find the SINGLE-unit row (label has "ml" but no
  // "x N" multiplier) so quantity = that many individual cans, not packs.
  function singleVariantIndex() {
    const adds = $$(VAR_ADD);
    const rows = $$('[aria-label*="rupee"]');
    const rowFor = (btn) => {
      const by = btn.getBoundingClientRect().top;
      let best = '', bd = 1e9;
      for (const r of rows) {
        const d = Math.abs(r.getBoundingClientRect().top - by);
        if (d < bd) { bd = d; best = r.getAttribute('aria-label'); }
      }
      return best || '';
    };
    for (let i = 0; i < adds.length; i++) {
      const l = rowFor(adds[i]);
      if (/\bml\b/i.test(l) && !/x\s*\d/i.test(l)) return i;
    }
    return -1;
  }

  async function swiggyBuy(qty) {
    qty = Math.max(1, qty | 0);
    if (!(await waitFor(ADD, 15000))) return { ok: false, reason: "NOPRODUCTS" };

    const first = $(ADD);
    first.scrollIntoView({ block: "center" });
    first.click();
    await sleep(1200);

    if ($(OVERLAY)) {
      if (!$(VAR_ADD)) return { ok: false, reason: "NOVARIANT" };
      let idx = singleVariantIndex();
      if (idx < 0) idx = 0;
      for (let k = 0; k < qty; k++) {
        const a = $$(VAR_ADD);
        if (a[idx]) a[idx].click();
        await sleep(350);
      }
      if ($(CLOSE_OV)) { $(CLOSE_OV).click(); await sleep(600); }
    } else {
      for (let k = 0; k < qty - 1; k++) { await sleep(400); if ($(ADD)) $(ADD).click(); }
    }

    if (!(await waitFor(CARTBAR, 6000))) return { ok: false, reason: "NOCART" };
    const bar = $(CARTBAR);
    const text = bar ? bar.innerText.replace(/\n/g, " | ") : "";
    if (!/item/i.test(text)) return { ok: false, reason: "NOCART", text };
    return { ok: true, text };   // checkout opens the cart itself
  }

  async function swiggyCheckout() {
    // The service worker opened us directly on the cart page. Let it render.
    await waitFor('[data-testid="veiwcartbar-container"], button, [role="button"]', 12000);
    await sleep(1500);
    if (/login|signin/i.test(location.href)) return { ok: false, reason: "LOGIN" };

    // 1. Proceed to Pay (main cart CTA)
    const proceed = findClickable(/proceed to pay|proceed to checkout|click to pay|^pay\b|place order|^checkout$/i);
    if (!proceed) return { ok: false, reason: "NOPROCEED", buttons: visibleButtons(), url: location.href };
    proceed.click();
    await sleep(3000);
    if (/login|signin/i.test(location.href)) return { ok: false, reason: "LOGIN" };

    // 2. Open the Wallet category (some layouts list Amazon Pay directly — skip if so)
    if (!findClickable(/amazon\s*pay/i)) {
      const wallet = findClickable(/^wallets?$|pay by wallet|wallet/i);
      if (wallet) { wallet.click(); await sleep(2000); }
    }

    // 3. Select Amazon Pay
    const amazon = findClickable(/amazon\s*pay/i);
    if (!amazon) return { ok: false, reason: "NOAMAZON", buttons: visibleButtons(), url: location.href };
    amazon.click();
    await sleep(2000);

    // 4. Final pay button (e.g. "Pay ₹203", "Place Order", "Pay Now")
    const pay = findClickable(/pay\s*[₹r]|pay now|place order|make payment|^pay\b|proceed to pay/i);
    if (!pay) return { ok: false, reason: "NOPAY", buttons: visibleButtons() };
    pay.click();
    await sleep(3500);

    const placed = /order placed|order confirmed|thank you|order id|successfully placed|payment successful/i
      .test(document.body.innerText || "");
    return placed ? { ok: true, reason: "PLACED" }
                  : { ok: false, reason: "SUBMITTED", buttons: visibleButtons() };
  }

  async function calendarSave() {
    const t0 = Date.now();
    let btn = null;
    while (Date.now() - t0 < 15000) {
      btn = $$('button, [role="button"]').find(e => {
        const lbl = (e.getAttribute("aria-label") || e.textContent || "").trim();
        return /^save$/i.test(lbl);
      });
      if (btn) break;
      await sleep(300);
    }
    if (!btn) return { ok: false, reason: "NOSAVE" };
    btn.click();
    await sleep(2500);
    return { ok: !/eventedit/.test(location.href), url: location.href };
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    (async () => {
      try {
        let result;
        if (msg.action === "ping")            result = { ok: true, url: location.href };
        else if (msg.action === "swiggy_buy") result = await swiggyBuy(msg.qty || 1);
        else if (msg.action === "swiggy_checkout") result = await swiggyCheckout();
        else if (msg.action === "calendar_save")   result = await calendarSave();
        else result = { ok: false, reason: "UNKNOWN_ACTION" };
        sendResponse(result);
      } catch (e) {
        sendResponse({ ok: false, reason: "EXCEPTION", error: String(e) });
      }
    })();
    return true;  // keep the message channel open for the async response
  });

  // Announce readiness so the SW wakes and connects its WebSocket.
  try { chrome.runtime.sendMessage({ type: "ready", url: location.href }); } catch (e) {}
})();
