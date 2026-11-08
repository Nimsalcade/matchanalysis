"""User-initiated match URL fetching and exact-label FotMob stat parsing.

No credentials, CAPTCHA workarounds, or access-control bypass are attempted.
A saved HTML page or manually verified numbers remain supported fallbacks.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import shutil
import unicodedata
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from tactic_decoder import load_match

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
TIMEOUT = (5, 25)
USER_AGENT = "MatchAnalysis/1.1 (user-requested single match report; local Streamlit app)"


class SourceError(ValueError):
    """Invalid input, blocked web fetch, or missing source data."""


class SourceHTTPError(SourceError):
    def __init__(self, site: str, status: int):
        self.status = status
        super().__init__(f"{site} returned HTTP {status}.")


class MissingEventData(SourceError):
    """The page did not provide a usable match event payload."""


@dataclass(frozen=True)
class FotMobStats:
    home_xg: float | None
    away_xg: float | None
    home_xgot: float | None
    away_xgot: float | None
    source: str
    extraction: str
    home_team: str | None = None
    away_team: str | None = None

    @property
    def complete(self) -> bool:
        return all(v is not None for v in (
            self.home_xg, self.away_xg, self.home_xgot, self.away_xgot
        ))


def check_source_url(raw: str, site: str) -> str:
    """Restrict all server-side requests to exactly the two supported hosts."""
    allowed = {"whoscored": "www.whoscored.com", "fotmob": "www.fotmob.com"}
    if site not in allowed:
        raise SourceError(f"Unknown source type: {site}")
    raw = (raw or "").strip()
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise SourceError(f"Enter a full HTTPS {site} match URL.") from exc
    if parts.scheme != "https" or parts.hostname not in {allowed[site], allowed[site][4:]}:
        raise SourceError(f"Enter a full HTTPS {site} match URL.")
    try:
        has_port = parts.port is not None
    except ValueError as exc:
        raise SourceError("Invalid URL port.") from exc
    if parts.username or parts.password or has_port:
        raise SourceError("URL credentials and custom ports are not supported.")
    match_path = parts.path.lower() if site == "whoscored" else parts.path
    if not match_path.startswith("/matches/"):
        raise SourceError("URL must point to a match page (/matches/...).")
    if site == "whoscored" and not re.match(r"^/matches/[0-9]+(?:/|$)", match_path):
        raise SourceError("WhoScored match URL must contain a numeric match ID.")
    if site == "fotmob" and not re.match(r"^/matches/[^/]+/[^/]+", parts.path):
        raise SourceError("FotMob match URL must contain its fixture path.")
    # URL fragment is UI state and never sent in HTTP requests.
    return parts._replace(fragment="").geturl()


def _http_html(url: str, site: str, session=None) -> bytes:
    client = session or requests.Session()
    current = check_source_url(url, site)
    for _ in range(4):
        try:
            resp = client.get(current, headers={"User-Agent": USER_AGENT, "Accept": "text/html"},
                              timeout=TIMEOUT, allow_redirects=False, stream=True)
        except requests.RequestException as exc:
            raise SourceError(f"{site.title()} page could not be fetched: {exc}") from exc
        try:
            if resp.status_code in (301, 302, 303, 307, 308):
                next_url = resp.headers.get("Location", "")
                if not next_url:
                    raise SourceError("Redirect contained no destination.")
                current = check_source_url(urljoin(current, next_url), site)
                continue
            if resp.status_code >= 400:
                raise SourceHTTPError("WhoScored" if site == "whoscored" else "FotMob", resp.status_code)
            kind = resp.headers.get("Content-Type", "").lower()
            if kind and not any(k in kind for k in ("html", "text/plain", "application/xhtml")):
                raise SourceError(f"Unexpected content type: {kind}.")
            buf = bytearray()
            for part in resp.iter_content(64 * 1024):
                buf.extend(part)
                if len(buf) > MAX_RESPONSE_BYTES:
                    raise SourceError("Source page exceeds the 16 MB download limit.")
            if not buf:
                raise SourceError("Source returned an empty HTML page.")
            return bytes(buf)
        except requests.RequestException as exc:
            raise SourceError(f"{site.title()} response could not be read: {exc}") from exc
        finally:
            resp.close()
    raise SourceError("Too many redirects.")


def _whoscored_fixture_id(url: str) -> str:
    return urlsplit(check_source_url(url, "whoscored")).path.split("/")[2]


def _restrict_whoscored_browser(context, page, fixture_id: str):
    # CDP also pauses redirect hops. Playwright's route handler sees only the first URL.
    cdp = context.new_cdp_session(page)
    main_frame = cdp.send("Page.getFrameTree")["frameTree"]["frame"]["id"]

    def check_request(event):
        request_id = {"requestId": event["requestId"]}
        request = event["request"]
        try:
            parts = urlsplit(request["url"])
            allowed = (parts.scheme == "https" and parts.netloc.lower() in
                       {"whoscored.com", "www.whoscored.com"} and request["method"] in {"GET", "HEAD"})
            if allowed and event["resourceType"] == "Document":
                allowed = (event.get("frameId") == main_frame and
                           _whoscored_fixture_id(request["url"]) == fixture_id)
        except ValueError:
            allowed = False
        if allowed:
            cdp.send("Fetch.continueRequest", request_id)
        else:
            cdp.send("Fetch.failRequest", {**request_id, "errorReason": "BlockedByClient"})

    cdp.on("Fetch.requestPaused", check_request)
    cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*"}]})
    context.route_web_socket("**/*", lambda socket: socket.close())


def _browser_html(url: str, site: str) -> bytes:
    """Optional ordinary browser rendering for client-side match data.

    Opens the user-supplied match URL without solving challenges or using proxy sites.
    """
    try:
        from playwright.sync_api import Error as BrowserError, TimeoutError as BrowserTimeout, sync_playwright
    except ImportError as exc:
        raise SourceError("Browser rendering requires 'pip install playwright' and "
                          "'python -m playwright install chromium'.") from exc
    url = check_source_url(url, site)
    label = "WhoScored" if site == "whoscored" else "FotMob"
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(
                    headless=True, executable_path=shutil.which("chromium"),
                    ignore_default_args=["--disable-popup-blocking"],
                )
            except BrowserError as exc:
                raise SourceError("Chromium could not start on the server. Install Chromium or run "
                                  "'python -m playwright install chromium'.") from exc
            try:
                context = browser.new_context(service_workers="block", accept_downloads=False)
                page = context.new_page()
                if site == "whoscored":
                    fixture_id = _whoscored_fixture_id(url)
                    _restrict_whoscored_browser(context, page, fixture_id)
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                if response is None:
                    raise SourceError(f"{label} returned no browser response.")
                if response.status >= 400:
                    raise SourceHTTPError(label, response.status)
                page.wait_for_timeout(1800)  # allow normal initial UI hydration
                if site == "whoscored" and _whoscored_fixture_id(page.url) != fixture_id:
                    raise SourceError("WhoScored redirected the browser to a different match.")
                if site == "fotmob":
                    try:
                        page.get_by_role("button", name="Stats", exact=True).first.click(timeout=3000)
                        page.wait_for_timeout(500)
                    except Exception:
                        pass  # URL fragment may already select the Stats tab
                html_bytes = page.content().encode("utf-8")
                if len(html_bytes) > MAX_RESPONSE_BYTES:
                    raise SourceError("Rendered page exceeds the 16 MB limit.")
                return html_bytes
            finally:
                browser.close()
    except BrowserTimeout as exc:
        raise SourceError(f"{label} browser navigation timed out.") from exc
    except BrowserError as exc:
        raise SourceError(f"{label} browser navigation failed or redirected outside the permitted match page.") from exc


def whoscored_teams(html: bytes, url: str = "") -> tuple[str, str]:
    """Validate the event payload and its fixture before handing it to either report."""
    try:
        match = load_match(html)
        centre = match["matchCentreData"]
        home, away = centre["home"]["name"], centre["away"]["name"]
        events = centre["events"]
    except (KeyError, ValueError, TypeError) as exc:
        raise MissingEventData("This WhoScored page has no readable match-centre event data.") from exc
    if not isinstance(events, list) or not events:
        raise MissingEventData("This WhoScored page has no match-centre events.")
    if not all(isinstance(name, str) and name.strip() for name in (home, away)):
        raise SourceError("Team names are missing from the WhoScored event data.")
    if url:
        fixture_id = _whoscored_fixture_id(url)
        if str(match.get("matchId")) != fixture_id:
            raise SourceError(f"This WhoScored HTML belongs to a different fixture than match {fixture_id}.")
    return home, away


def fetch_whoscored(url: str, browser_fallback: bool = False, session=None) -> bytes:
    url = check_source_url(url, "whoscored")
    try:
        html = _http_html(url, "whoscored", session=session)
        whoscored_teams(html, url)
    except (SourceHTTPError, MissingEventData) as direct_error:
        if not browser_fallback or (isinstance(direct_error, SourceHTTPError) and direct_error.status != 403):
            raise
        try:
            html = _browser_html(url, "whoscored")
            whoscored_teams(html, url)
        except SourceError as browser_error:
            raise SourceError(f"Direct request: {direct_error} Browser request: {browser_error}") from browser_error
    return html


def _float_value(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (float, int)):
        return float(value) if 0 <= value <= 20 else None
    if isinstance(value, str):
        match = re.fullmatch(r"\s*(\d{1,2}(?:[.,]\d{1,3})?)\s*", value)
        if match:
            v = float(match.group(1).replace(",", "."))
            return v if 0 <= v <= 20 else None
    if isinstance(value, dict):
        for k in ("value", "displayValue", "statValue", "number"):
            if k in value:
                found = _float_value(value[k])
                if found is not None:
                    return found
    return None


def _stat_key(label: str) -> str | None:
    text = re.sub(r"\s+", " ", str(label)).strip().lower()
    if text in ("expected goals (xg)", "expected goals", "xg"):
        return "xg"
    if text in ("xg on target (xgot)", "expected goals on target (xgot)",
                "expected goals on target", "xgot", "xg on target"):
        return "xgot"
    return None


def _pair_from_node(node: dict):
    # Explicit home/away is preferable to a positional list.
    for a, b in (("homeValue", "awayValue"), ("home", "away"),
                 ("homeStat", "awayStat"), ("homeTeam", "awayTeam")):
        if a in node and b in node:
            x, y = _float_value(node[a]), _float_value(node[b])
            if x is not None and y is not None:
                return x, y
    for key in ("stats", "values", "value", "numbers", "data"):
        obj = node.get(key)
        if isinstance(obj, (list, tuple)) and len(obj) == 2:
            x, y = _float_value(obj[0]), _float_value(obj[1])
            if x is not None and y is not None:
                return x, y
        if isinstance(obj, dict):
            pair = _pair_from_node(obj)
            if pair:
                return pair
    return None


def _stats_from_json_blob(obj):
    """Find exact labeled stat rows in nested page hydration JSON."""
    found = {}
    to_visit = [obj]
    seen = 0
    while to_visit and seen < 300000:
        cur = to_visit.pop()
        seen += 1
        if isinstance(cur, dict):
            label = next((cur.get(k) for k in ("title", "label", "name", "statName")
                          if isinstance(cur.get(k), str) and _stat_key(cur[k])), None)
            if label:
                kind = _stat_key(label)
                pair = _pair_from_node(cur)
                if pair is not None:
                    found[kind] = pair
            to_visit.extend(cur.values())
        elif isinstance(cur, list):
            to_visit.extend(cur)
    return found


def _dom_stats(soup):
    """Read a rendered label-centered row, retaining home/away column order."""
    found = {}
    for node in soup.find_all(string=True):
        label = str(node).strip()
        kind = _stat_key(label)
        if not kind or kind in found:
            continue
        # A row is typically home number | name | away number.
        for par in list(node.parents)[:5]:
            tokens = list(par.stripped_strings)
            idx = next((i for i, text in enumerate(tokens) if _stat_key(text) == kind), -1)
            if idx < 1 or idx >= len(tokens) - 1:
                continue
            pre = next((_float_value(v) for v in reversed(tokens[:idx]) if _float_value(v) is not None), None)
            post = next((_float_value(v) for v in tokens[idx + 1:] if _float_value(v) is not None), None)
            if pre is not None and post is not None:
                found[kind] = pre, post
                break
    return found


def parse_fotmob_stats(html: str | bytes, source: str = "uploaded FotMob HTML") -> FotMobStats:
    text = html.decode("utf-8", "replace") if isinstance(html, bytes) else html
    soup = BeautifulSoup(text, "html.parser")
    found = {}
    provenance = []
    # Includes __NEXT_DATA__ and ordinary application/json script tags.
    for script in soup.find_all("script"):
        candidate = script.string or script.get_text()
        if not candidate:
            continue
        candidate = candidate.strip()
        if not candidate.startswith(("{", "[")):
            continue
        try:
            embedded = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        extracted = _stats_from_json_blob(embedded)
        if extracted:
            found.update(extracted)
            provenance.append("embedded JSON")
    dom = _dom_stats(soup)
    for k, v in dom.items():
        found.setdefault(k, v)
    if dom:
        provenance.append("rendered HTML")
    xg = found.get("xg", (None, None))
    xgot = found.get("xgot", (None, None))
    title_tag = soup.find("title")
    page_title = title_tag.get_text(" ", strip=True) if title_tag else ""
    names = re.search(r"^(.+?)\s+vs\s+(.+?)(?:\s+[-–|]\s+.*)?$", page_title, re.I)
    home_team = names.group(1).strip() if names else None
    away_team = names.group(2).strip() if names else None
    return FotMobStats(xg[0], xg[1], xgot[0], xgot[1], source,
                       "+".join(provenance) or "not found", home_team, away_team)


def fetch_fotmob(url: str, browser_fallback: bool = False, session=None) -> FotMobStats:
    url = check_source_url(url, "fotmob")
    result = parse_fotmob_stats(_http_html(url, "fotmob", session=session), url)
    if result.complete or not browser_fallback:
        return result
    try:
        rendered = parse_fotmob_stats(_browser_html(url, "fotmob"), url)
    except SourceError:
        if result.home_xg is None and result.home_xgot is None:
            raise
    else:
        if rendered.complete or (result.home_xg is None and result.home_xgot is None):
            return rendered
    return result


def fixture_names_match(home: str, away: str, stats: FotMobStats) -> bool | None:
    """True/False for matching fixture; None if FotMob title lacks both names."""
    if not stats.home_team or not stats.away_team or not home or not away:
        return None
    def normalize(s):
        base = unicodedata.normalize("NFKD", s.casefold())
        name = "".join(c for c in base if c.isalnum() and not unicodedata.combining(c))
        return {"bayernmunich": "bayernmunchen"}.get(name, name)
    def equivalent(a, b):
        a, b = normalize(a), normalize(b)
        return a == b or (min(len(a), len(b)) >= 5 and (a.endswith(b) or b.endswith(a)))
    return equivalent(home, stats.home_team) and equivalent(away, stats.away_team)
