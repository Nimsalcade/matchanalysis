const APP_ORIGINS = new Set([
  "https://matchareport.streamlit.app",
  "http://localhost:8501",
  "http://127.0.0.1:8501",
]);
const MAX_PAYLOAD_BYTES = 8 * 1024 * 1024;
const NO_EVENTS = "WhoScored opened in your browser, but this Live page did not contain a match event feed.";
const WRONG_PAGE = "WhoScored did not open the requested Live match page. Check the match URL and your access in the opened tab.";

function liveMatch(url) {
  if (typeof url !== "string" || url.length > 2048) return null;
  let page;
  try {
    page = new URL(url);
  } catch {
    return null;
  }
  if (page.protocol !== "https:" || !["www.whoscored.com", "whoscored.com"].includes(page.hostname) ||
      page.port || page.username || page.password) return null;
  const match = /^\/matches\/(\d+)\/live(?:\/|$)/i.exec(page.pathname);
  return match ? {url: page.href, id: match[1]} : null;
}

function allowedSender(sender) {
  try {
    const {origin, pathname} = new URL(sender.url);
    const hostedAppFrame = origin === "https://matchareport.streamlit.app" && pathname === "/~/+/";
    return sender.id === chrome.runtime.id && APP_ORIGINS.has(origin) &&
      (sender.frameId === 0 || hostedAppFrame);
  } catch {
    return false;
  }
}

function waitForTab(tabId) {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => finish(null, new Error("WhoScored took too long to load in your browser.")), 22000);
    function finish(tab, error) {
      clearTimeout(timeout);
      chrome.tabs.onUpdated.removeListener(updated);
      chrome.tabs.onRemoved.removeListener(removed);
      if (error) reject(error);
      else resolve(tab);
    }
    function updated(id, change, tab) {
      if (id === tabId && change.status === "complete" && tab.url !== "about:blank") finish(tab);
    }
    function removed(id) {
      if (id === tabId) finish(null, new Error("The WhoScored tab was closed before the match loaded."));
    }
    chrome.tabs.onUpdated.addListener(updated);
    chrome.tabs.onRemoved.addListener(removed);
    chrome.tabs.get(tabId).then((tab) => {
      if (tab.url && tab.url !== "about:blank" && tab.status === "complete") finish(tab);
    }, () => removed(tabId));
  });
}

// Chrome executes this function in the opened tab's isolated world.
function readInlineEvents(expectedId, maxBytes) {
  const match = /^\/matches\/(\d+)\/live(?:\/|$)/i.exec(location.pathname);
  if (location.protocol !== "https:" || location.port ||
      !["www.whoscored.com", "whoscored.com"].includes(location.hostname) || match?.[1] !== expectedId) {
    return {wrongPage: true};
  }
  const status = performance.getEntriesByType("navigation")[0]?.responseStatus;
  if (status >= 400) return {status};
  for (const script of document.scripts) {
    if (script.src) continue;
    const text = script.textContent;
    const start = text.search(/require\.config\.params\[\s*["']args["']\s*\]\s*=\s*\{/);
    if (start < 0) continue;
    const payload = text.slice(start);
    if (!/\bmatchCentreData\b/.test(payload) || !/"events"\s*:\s*\[\s*\{/.test(payload)) continue;
    const html = `<!doctype html><script type="text/plain">${payload}</script>`;
    if (new TextEncoder().encode(html).length > maxBytes) return {tooLarge: true};
    return {html};
  }
  return {};
}

async function fetchMatch(url) {
  const match = liveMatch(url);
  if (!match) return {html: null, error: "Enter an HTTPS WhoScored Live URL with a numeric match ID."};

  let tab;
  try {
    tab = await chrome.tabs.create({url: match.url, active: false});
  } catch {
    return {html: null, error: "Chrome could not open the WhoScored tab."};
  }

  let loaded;
  try {
    loaded = await waitForTab(tab.id);
  } catch (error) {
    return {html: null, error: error.message};
  }
  if (liveMatch(loaded.url)?.id !== match.id) return {html: null, error: WRONG_PAGE};

  let result;
  try {
    [{result}] = await chrome.scripting.executeScript({
      target: {tabId: tab.id},
      func: readInlineEvents,
      args: [match.id, MAX_PAYLOAD_BYTES],
    });
  } catch {
    return {html: null, error: "Chrome could not read the WhoScored match tab. Check your access in the opened tab."};
  }
  if (result?.wrongPage) return {html: null, error: WRONG_PAGE};
  if (result?.status) return {html: null, error: `WhoScored returned HTTP ${result.status} in this browser.`};
  if (result?.tooLarge) return {html: null, error: "The browser match payload exceeds the 8 MB limit."};
  if (!result?.html) return {html: null, error: NO_EVENTS};
  await chrome.tabs.remove(tab.id).catch(() => {});
  return {html: result.html, error: null};
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (message?.type !== "matchanalysis:who-fetch" || !allowedSender(sender)) return;
  fetchMatch(message.url).then(respond, () => respond({html: null, error: "Browser match fetch failed."}));
  return true;
});
