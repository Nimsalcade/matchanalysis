(() => {
  const appOrigins = new Set([
    "https://matchareport.streamlit.app",
    "http://localhost:8501",
    "http://127.0.0.1:8501",
  ]);
  const origin = window.location.origin;
  if (!appOrigins.has(origin)) return;

  const pending = new Set();
  function isFetchFrame(source) {
    return Array.from(document.querySelectorAll("iframe[src]")).some((frame) => {
      if (frame.contentWindow !== source) return false;
      const url = new URL(frame.src, window.location.href);
      return url.origin === origin &&
        /^\/component\/(?:[^/]+\.)?browser_fetch_component(?:\/|$)/.test(url.pathname);
    });
  }

  window.addEventListener("message", async (event) => {
    const request = event.data;
    if (event.origin !== origin || request?.type !== "matchanalysis:who-request" ||
        !isFetchFrame(event.source)) return;
    const {url, request_id} = request;
    if (typeof url !== "string" || url.length > 2048 ||
        typeof request_id !== "string" || !request_id || request_id.length > 128 ||
        pending.has(request_id)) return;

    pending.add(request_id);
    event.source.postMessage({type: "matchanalysis:who-ack", url, request_id}, origin);
    let result;
    try {
      result = await chrome.runtime.sendMessage({type: "matchanalysis:who-fetch", url});
      if (!result) throw new Error("No browser extension response");
    } catch {
      result = {html: null, error: "The browser extension stopped responding. Reload this app and try again."};
    } finally {
      pending.delete(request_id);
    }
    event.source.postMessage({type: "matchanalysis:who-result", url, request_id, ...result}, origin);
  });
})();
