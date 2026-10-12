// Observe responses the page already requests; never change request headers or browser identity.
(() => {
  if (window.__xianyuNativeCapture) return;
  const capture = { entries: [], sequence: 0 };
  window.__xianyuNativeCapture = capture;
  const relevant = /mtop\.(?:taobao\.idlemtopsearch\.pc\.search|taobao\.idle\.pc\.detail|idle\.web\.(?:user\.page\.head|xyh\.item\.list|trade\.rate\.list))/i;
  function remember(url, text) {
    if (!relevant.test(String(url)) || typeof text !== "string" || text.length > 1500000) return;
    try {
      const trimmed = text.trim();
      const body = JSON.parse(trimmed.startsWith("{") ? trimmed : trimmed.slice(trimmed.indexOf("(") + 1, trimmed.lastIndexOf(")")));
      capture.entries.push({ url: String(url), body, sequence: ++capture.sequence });
      if (capture.entries.length > 60) capture.entries.shift();
    } catch (_) { /* A non-JSON response has no collection data. */ }
  }
  const observedCallbacks = new WeakSet();
  function observeJsonp(script) {
    if (script.tagName !== "SCRIPT" || !script.src || !relevant.test(script.src)) return;
    try {
      const url = script.src;
      const name = new URL(url).searchParams.get("callback");
      if (!name || !/^[A-Za-z_$][\w$]*$/.test(name)) return;
      const original = window[name];
      if (typeof original !== "function" || observedCallbacks.has(original)) return;
      const wrapped = function (...args) {
        try { remember(url, JSON.stringify(args[0])); } catch (_) {}
        return original.apply(this, args);
      };
      observedCallbacks.add(wrapped);
      window[name] = wrapped;
    } catch (_) {}
  }
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === "attributes") observeJsonp(record.target);
      for (const node of record.addedNodes || []) {
        observeJsonp(node);
        node.querySelectorAll?.("script[src]").forEach(observeJsonp);
      }
    }
  }).observe(document, { childList: true, subtree: true, attributes: true, attributeFilter: ["src"] });
  document.querySelectorAll("script[src]").forEach(observeJsonp);
  const originalFetch = window.fetch;
  window.fetch = function (...args) {
    const result = originalFetch.apply(this, args);
    result.then(response => {
      if (relevant.test(response.url)) response.clone().text().then(text => remember(response.url, text)).catch(() => {});
    }, () => {});
    return result;
  };
  const originalOpen = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function (method, url, ...args) {
    this.addEventListener("load", () => {
      try {
        const text = this.responseType === "json" ? JSON.stringify(this.response) : this.responseText;
        remember(this.responseURL || url, text);
      } catch (_) {}
    }, { once: true });
    return originalOpen.call(this, method, url, ...args);
  };
})();
