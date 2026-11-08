import streamlit as st
import os
import subprocess
import glob
from PIL import Image
import tempfile
import io
import sys
import time
from match_sources import (
    SourceError, fetch_fotmob, fetch_whoscored, fixture_names_match, whoscored_teams,
)

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title="Scout Report Generator",
    page_icon="⚽",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# CUSTOM CSS
# ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=Barlow+Condensed:wght@400;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Barlow Condensed', sans-serif;
}
.main { background-color: #0d1117; }
h1, h2, h3 { font-family: 'Barlow Condensed', sans-serif; letter-spacing: 0.5px; }

/* Sidebar */
section[data-testid="stSidebar"] {
    background-color: #161b22;
    border-right: 1px solid #30363d;
}
section[data-testid="stSidebar"] * { color: #c9d1d9 !important; }

/* Match cards */
.match-card {
    background: #161b22;
    border: 1px solid #30363d;
    border-left: 3px solid #f78166;
    border-radius: 6px;
    padding: 1rem 1.25rem;
    margin-bottom: 1rem;
}
.match-label {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 11px;
    color: #8b949e;
    letter-spacing: 1px;
    text-transform: uppercase;
    margin-bottom: 0.5rem;
}

/* Buttons */
.stButton > button {
    font-family: 'Barlow Condensed', sans-serif;
    font-weight: 600;
    letter-spacing: 0.5px;
    border-radius: 4px;
    border: none;
    transition: all 0.15s ease;
}
.stButton > button[kind="primary"] {
    background: #f78166 !important;
    color: #0d1117 !important;
}
.stButton > button[kind="primary"]:hover {
    background: #ff9580 !important;
    transform: translateY(-1px);
}
.stButton > button[kind="secondary"] {
    background: #21262d !important;
    color: #8b949e !important;
    border: 1px solid #30363d !important;
}

/* Number inputs */
.stNumberInput input {
    font-family: 'IBM Plex Mono', monospace;
    font-size: 13px;
    background: #0d1117;
    border: 1px solid #30363d;
    color: #c9d1d9;
    border-radius: 4px;
}

/* Radio */
.stRadio label { font-size: 14px; }

/* Progress */
.stProgress > div > div { background-color: #f78166; }

/* Expander */
.streamlit-expanderHeader {
    background: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 4px !important;
    font-family: 'IBM Plex Mono', monospace !important;
    font-size: 12px !important;
    color: #8b949e !important;
}

/* Download button */
.stDownloadButton > button {
    background: #238636 !important;
    color: white !important;
    font-family: 'Barlow Condensed', sans-serif !important;
    font-weight: 600 !important;
    font-size: 16px !important;
    letter-spacing: 0.5px !important;
    padding: 0.6rem 2rem !important;
    border-radius: 4px !important;
    border: none !important;
    width: 100%;
}
.stDownloadButton > button:hover {
    background: #2ea043 !important;
}

/* Success / error / warning */
.stSuccess { border-left: 3px solid #3fb950; }
.stError   { border-left: 3px solid #f85149; }
.stWarning { border-left: 3px solid #d29922; }

/* Divider */
hr { border-color: #21262d; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
def get_images_after_timestamp(ts: float):
    """Return (dashboard_img, player_dashboard_img) created after `ts`."""
    output_dir = "match report analysis"
    if not os.path.exists(output_dir):
        return None, None

    files = [f for f in glob.glob(os.path.join(output_dir, "*.png"))
             if os.path.getmtime(f) > ts]
    files.sort(key=os.path.getmtime, reverse=True)

    dash_img, player_img = None, None
    for f in files:
        if "Player_Dashboard" in f and not player_img:
            player_img = f
        elif "Dashboard" in f and not dash_img:
            dash_img = f
    return dash_img, player_img


def images_to_pdf(image_paths: list) -> bytes:
    """Merge a list of image paths into a single PDF bytes object."""
    pil_imgs = []
    for p in image_paths:
        try:
            img = Image.open(p).convert("RGB")
            pil_imgs.append(img)
        except Exception:
            pass

    if not pil_imgs:
        return b""

    buf = io.BytesIO()
    pil_imgs[0].save(
        buf,
        format="PDF",
        save_all=True,
        append_images=pil_imgs[1:],
        resolution=150,
    )
    buf.seek(0)
    return buf.read()


STAT_ATTRIBUTES = {
    "hxg": "home_xg", "axg": "away_xg", "hxgot": "home_xgot", "axgot": "away_xgot",
}


def clear_imported_stats(index, cached):
    """Clear only values that still match the previous automatic import."""
    if not cached or not cached.get("applied"):
        return
    for field, attribute in STAT_ATTRIBUTES.items():
        key = f"{field}_{index}"
        value = getattr(cached["stats"], attribute)
        if value is not None and st.session_state.get(key) == f"{value:.2f}":
            st.session_state[key] = ""


def request_report():
    st.session_state.generate_requested = True


# ─────────────────────────────────────────────
# SESSION STATE INIT
# ─────────────────────────────────────────────
if "match_count" not in st.session_state:
    st.session_state.match_count = 1
if "results" not in st.session_state:
    st.session_state.results = []   # list of {"dash": path, "player": path, "label": str}
if "detected_home" not in st.session_state:
    st.session_state.detected_home = ""
if "detected_away" not in st.session_state:
    st.session_state.detected_away = ""


# ─────────────────────────────────────────────
# SIDEBAR — GLOBAL SETTINGS
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("## ⚽ Scout Report Generator")
    st.markdown("<hr/>", unsafe_allow_html=True)

    st.markdown("### Analysis Mode")

    # Build radio options using detected names (or generic fallbacks)
    _home = st.session_state.detected_home
    _away = st.session_state.detected_away
    if _home and _away:
        radio_options = [
            "Both Teams",
            f"{_home} (Home)",
            f"{_away} (Away)",
        ]
    else:
        radio_options = [
            "Both Teams",
            "Home Team Only",
            "Away Team Only",
        ]

    team_focus = st.radio(
        "Which team to analyse?",
        radio_options,
        index=0,
        help="Select a specific team to generate a single-team report. "
             "Team names appear after fetching or uploading the match HTML.",
    )

    # Derive the side keyword from the selection
    if team_focus == "Both Teams":
        team_focus_side = "both"
    elif "(Home)" in team_focus or team_focus == "Home Team Only":
        team_focus_side = "home"
    else:
        team_focus_side = "away"

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.markdown("### Matches")
    st.caption(f"Currently configured: **{st.session_state.match_count}** match(es)")

    col_add, col_reset = st.columns(2)
    with col_add:
        if st.button("➕ Add", use_container_width=True):
            st.session_state.match_count += 1
            st.rerun()
    with col_reset:
        if st.button("🗑 Clear", use_container_width=True):
            st.session_state.match_count = 1
            st.session_state.results = []
            st.session_state.pop("tactical_result", None)
            st.session_state.detected_home = ""
            st.session_state.detected_away = ""
            for key in tuple(st.session_state.keys()):
                if key.startswith(("match_mode_", "whoscored_url_", "fotmob_url_", "file_",
                                   "match_download_", "fotmob_fetch_", "label_",
                                   "hxg_", "hxgot_", "axg_", "axgot_")):
                    st.session_state.pop(key)
            st.rerun()

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.caption(
        "**Tip:** Paste both match URLs and click Generate. "
        "The app downloads and imports the match sources automatically."
    )


# ─────────────────────────────────────────────
# MAIN — HEADER
# ─────────────────────────────────────────────
st.markdown("## Post-Match Report Generator")

if team_focus_side != "both":
    _focus_name = _home if team_focus_side == "home" else _away
    _mode_label = f"🎯 **{_focus_name}**" if _focus_name else f"**{team_focus}**"
else:
    _mode_label = "**Both Teams**"

st.markdown(
    f"Target: {_mode_label} &nbsp;|&nbsp; Matches queued: **{st.session_state.match_count}**"
)
st.markdown("<hr/>", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# MAIN — MATCH INPUT GRID
# ─────────────────────────────────────────────
generate_requested = st.session_state.pop("generate_requested", False)
match_inputs = []   # downloaded or uploaded bytes, match label and verified metric values

for i in range(st.session_state.match_count):
    st.markdown(f"<div class='match-label'>MATCH {i + 1}</div>", unsafe_allow_html=True)
    with st.container():
        source_mode = st.radio(
            f"Match {i + 1} input", ["Fetch by URL", "Upload saved HTML"],
            key=f"match_mode_{i}", horizontal=True,
        )
        who_url = ""
        if source_mode == "Fetch by URL":
            who_url = st.text_input(
                "WhoScored Live match URL", key=f"whoscored_url_{i}",
                placeholder="https://www.whoscored.com/matches/1995784/live/...",
            ).strip()

        fotmob_url = st.text_input(
            "FotMob match URL — xG and xGOT", key=f"fotmob_url_{i}",
            placeholder="https://www.fotmob.com/matches/...#123456:tab=stats",
        ).strip()
        who_key, fot_key = f"match_download_{i}", f"fotmob_fetch_{i}"
        who = st.session_state.get(who_key, {})
        if who.get("url") != who_url:
            who = {}
        fot = st.session_state.get(fot_key, {})
        if fot and fot["url"] != fotmob_url:
            clear_imported_stats(i, fot)
            st.session_state.pop(fot_key)
            fot = {}

        fetch_clicked = st.button("Fetch match sources", key=f"fetch_{i}", use_container_width=True)
        fetch_who = who_url and (fetch_clicked or (generate_requested and "html" not in who))
        fetch_fot = fotmob_url and (fetch_clicked or (generate_requested and "stats" not in fot))
        if fetch_who or fetch_fot:
            with st.spinner("Reading the supplied match pages…"):
                if fetch_who:
                    try:
                        html = fetch_whoscored(who_url, browser_fallback=True)
                        who = {"url": who_url, "html": html, "source": "server"}
                    except SourceError as exc:
                        who = {"url": who_url, "error": str(exc)}
                    st.session_state[who_key] = who
                if fetch_fot:
                    try:
                        stats = fetch_fotmob(fotmob_url)
                    except SourceError as exc:
                        fot = {**fot, "url": fotmob_url, "error": str(exc)}
                    else:
                        clear_imported_stats(i, fot)
                        fot = {"url": fotmob_url, "stats": stats, "applied": False,
                               "replace_values": fetch_clicked}
                    st.session_state[fot_key] = fot
        elif fetch_clicked:
            st.info("Enter a match URL to fetch.")

        if who.get("error"):
            st.error(f"WhoScored server fetch: {who['error']}")
        uploaded = None
        if source_mode == "Upload saved HTML":
            uploaded = st.file_uploader(
                "WhoScored saved HTML", type=["html", "htm"], key=f"file_{i}",
            )

        html_bytes = uploaded.getvalue() if uploaded is not None else who.get("html")
        home_name, away_name = "", ""
        if html_bytes is not None:
            try:
                home_name, away_name = whoscored_teams(html_bytes, who_url)
            except SourceError as exc:
                st.error(f"WhoScored HTML: {exc}")
                html_bytes = None
            else:
                origin = "upload" if uploaded is not None else who["source"]
                st.caption(f"Match event teams: **{home_name}** (H) vs **{away_name}** (A) · WhoScored {origin}")
                if not st.session_state.detected_home:
                    st.session_state.detected_home = home_name
                    st.session_state.detected_away = away_name
        if fot.get("error"):
            st.warning(f"FotMob: {fot['error']} Enter missing values below.")
        stats = fot.get("stats")
        stats_mismatch = False
        if stats:
            check = fixture_names_match(home_name, away_name, stats)
            stats_mismatch = check is False
            if stats_mismatch:
                st.error("FotMob and WhoScored identify different home/away teams. "
                         "Correct the FotMob URL or clear it and enter verified values manually.")
            else:
                if not fot["applied"]:
                    for field, attribute in STAT_ATTRIBUTES.items():
                        value = getattr(stats, attribute)
                        key = f"{field}_{i}"
                        if value is not None and (fot.get("replace_values", True) or not st.session_state.get(key)):
                            st.session_state[key] = f"{value:.2f}"
                    fot = {**fot, "applied": True}
                    st.session_state[fot_key] = fot
                if check is None:
                    st.info("FotMob team names could not be cross-checked against WhoScored events.")
                if not stats.complete:
                    st.warning("FotMob returned partial stats. Fill in the missing values manually.")
                st.caption(f"FotMob source: {stats.extraction}; xG and xGOT values below can be edited.")
            if stats.home_team and stats.away_team:
                st.caption(f"FotMob teams: **{stats.home_team}** (H) vs **{stats.away_team}** (A)")

        match_label = st.text_input(
            "Match label", key=f"label_{i}",
            placeholder="e.g. Wolfsburg vs Darmstadt",
        )
        if not match_label.strip():
            match_label = f"{home_name} vs {away_name}" if home_name else f"Match {i + 1}"

        st.markdown("**Match statistics (home and away)**")
        st.caption("The app fills these fields from the FotMob URL. You can also enter values manually. "
                   "Blank means unknown, never zero. The legacy PDF renderer needs all four fields.")
        c1, c2, c3, c4 = st.columns(4)
        stat_fields = (
            (c1, "hxg", f"{home_name or 'Home'} xG"),
            (c2, "hxgot", f"{home_name or 'Home'} xGOT"),
            (c3, "axg", f"{away_name or 'Away'} xG"),
            (c4, "axgot", f"{away_name or 'Away'} xGOT"),
        )
        parsed_values = {}
        for column, name, label in stat_fields:
            with column:
                value = st.text_input(label, key=f"{name}_{i}", placeholder="Not available")
                if value.strip():
                    try:
                        number = float(value)
                        if not (0 <= number <= 20):
                            raise ValueError("out of range")
                        parsed_values[name] = number
                    except ValueError:
                        parsed_values[name] = None
                        st.error("Enter 0–20 (e.g. 2.28)")
                else:
                    parsed_values[name] = None

        match_inputs.append({
            "html_bytes": html_bytes, "label": match_label,
            "configured": bool(who_url or fotmob_url or uploaded),
            "stats_mismatch": stats_mismatch,
            "home_name": home_name, "away_name": away_name,
            "home_xg": parsed_values["hxg"], "home_xgot": parsed_values["hxgot"],
            "away_xg": parsed_values["axg"], "away_xgot": parsed_values["axgot"],
        })
    st.markdown("<hr/>", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# PRECISION TACTICAL DECODER (independent of the legacy PNG/PDF pipeline)
# ─────────────────────────────────────────────
with st.expander("Tactical decoder — event evidence and match phases", expanded=False):
    st.caption(
        "Reads the embedded WhoScored events directly. The recorded formation is confirmed; "
        "pass receivers and possession segments are estimates. Off-ball shapes need tracking/video."
    )
    tactical_team = st.text_input(
        "Team to decode across all fetched/uploaded matches (regardless of home/away side)",
        value=st.session_state.detected_home or "",
        key="tactical_team_name",
    )
    if st.button("Decode tactics from loaded match data", use_container_width=True):
        try:
            from tactic_decoder import analyze_match, aggregate_team, select_team, as_markdown
            tactical_matches = [analyze_match(m["html_bytes"]) for m in match_inputs if m["html_bytes"] is not None]
            if not tactical_matches:
                st.error("Fetch a WhoScored match URL or upload at least one HTML file first.")
            else:
                tactical_aggregate = aggregate_team(tactical_matches, tactical_team)
                st.session_state.tactical_result = {
                    "matches": tactical_matches,
                    "aggregate": tactical_aggregate,
                    "team": tactical_team,
                }
        except (ValueError, KeyError, TypeError) as exc:
            st.error(f"Tactical analysis failed: {exc}")

    if st.session_state.get("tactical_result"):
        from tactic_decoder import select_team, as_markdown
        import json as _tjson
        import pandas as _tpd

        data = st.session_state.tactical_result
        aggregation = data["aggregate"]
        totals = aggregation["passes_total"]
        st.markdown(f"**{aggregation['team']}** · {aggregation['match_count']} decoded match(es)")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Completed passes", f"{totals['completed']}/{totals['attempts']}")
        c2.metric("Progressive passes", totals["progressive_passes"])
        c3.metric("Half-space receipts", totals["halfspace_passes"])
        c4.metric("Final-third entries", totals["final_third_entries"])
        st.download_button(
            "Download complete tactical evidence (JSON)",
            data=_tjson.dumps(data, ensure_ascii=False, indent=2),
            file_name="tactical_decoder.json", mime="application/json",
            use_container_width=True, key="download_tactical_json",
        )
        tabs = st.tabs([f"{m['home']} vs {m['away']}" for m in data["matches"]])
        for tab, m in zip(tabs, data["matches"]):
            with tab:
                t = select_team(m, data["team"])
                st.markdown(f"**Registered starting shape: {t['first_registered_formation']}**")
                st.caption("Formation intervals come from the match-centre HTML, not a reconstructed off-ball shape.")
                st.dataframe(_tpd.DataFrame(t["registered_formations"]), hide_index=True, use_container_width=True)
                st.markdown("**Before/after registered formation changes (rates per 90)**")
                st.dataframe(_tpd.DataFrame(t["formation_phase_comparison"]), hide_index=True, use_container_width=True)
                st.markdown("**Passing, progression and attacking zones**")
                st.dataframe(_tpd.DataFrame([t["passing"]]).T.rename(columns={0: "Value"}), use_container_width=True)
                st.markdown("**Directed passing connections (inferred receptions)**")
                st.dataframe(_tpd.DataFrame(t["top_directed_passing_connections"]), hide_index=True, use_container_width=True)
                st.caption(t["pass_link_quality"]["warning"])
                st.markdown("**Recorded passers in the build-up to shots**")
                st.dataframe(_tpd.DataFrame(t["shot_sequences"]), hide_index=True, use_container_width=True)
                st.markdown("**Player participation and on-ball positions**")
                st.dataframe(_tpd.DataFrame(t["players"]), hide_index=True, use_container_width=True)
                st.markdown("**15-minute event trends**")
                st.dataframe(_tpd.DataFrame.from_dict(t["time_slices"], orient="index"), use_container_width=True)
                st.markdown("**Defensive-event evidence**")
                st.json(t["defence_event_proxies"], expanded=False)
                st.download_button(
                    "Download tactical interpretation (Markdown)",
                    data=as_markdown(m, data["team"]),
                    file_name=f"tactic_{m['match_id']}.md", mime="text/markdown",
                    key=f"tactics_markdown_{m['match_id']}",
                )
        st.info("This report does not estimate the exact off-ball formation, high-press triggers, or rest-defence without tracking/video.")


# ─────────────────────────────────────────────
# GENERATE BUTTON
# ─────────────────────────────────────────────
st.button(
    f"🚀  Generate {st.session_state.match_count} Report(s) & Export PDF",
    type="primary",
    use_container_width=True,
    on_click=request_report,
)

if generate_requested:
    valid = [m for m in match_inputs if m["html_bytes"] is not None]

    missing_events = [m["label"] for m in match_inputs if m["configured"] and m["html_bytes"] is None]
    if missing_events:
        st.error("WhoScored events are unavailable for: " + ", ".join(missing_events) +
                 ". Check the source status above.")
        st.stop()
    if not valid:
        st.error("Fetch WhoScored match-centre events from a Live URL or upload saved match-centre HTML.")
        st.stop()

    mismatches = [m["label"] for m in valid if m["stats_mismatch"]]
    if mismatches:
        st.error("Correct the FotMob fixture for: " + ", ".join(mismatches) + ".")
        st.stop()

    # Require real, sourced values; never synthesize xG/xGOT defaults.
    missing_stats = [m["label"] for m in valid if any(m[k] is None for k in
        ("home_xg", "away_xg", "home_xgot", "away_xgot"))]
    if missing_stats:
        st.error("xG and xGOT are missing or invalid for: " + ", ".join(missing_stats) +
                 ". Fetch from FotMob or enter the four numbers manually.")
        st.stop()
    all_generated_images = []   # flat list of image paths, in order
    errors = []
    st.session_state.results = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, cfg in enumerate(valid):
        html_bytes = cfg["html_bytes"]

        # ── Determine --team arg from sidebar selection ───────────────
        team_arg = team_focus_side   # "both", "home", or "away"
        side_display = {"home": "HOME", "away": "AWAY", "both": "BOTH"}
        status_text.markdown(
            f"⚙️  Processing **{cfg['label']}** ({idx + 1}/{len(valid)}) "
            f"— analysing **{side_display[team_arg]}** side…"
        )

        # Write HTML to a temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as tmp:
            tmp.write(html_bytes)
            tmp_path = tmp.name

        # Record timestamp so we can find newly created images afterward
        ts_before = time.time() - 1

        cmd = [
            sys.executable,
            "PostMatchDashboard.py",
            tmp_path,
            str(cfg["home_xg"]),
            str(cfg["away_xg"]),
            str(cfg["home_xgot"]),
            str(cfg["away_xgot"]),
            "--team", team_arg,
        ]

        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
        )
        stdout, stderr = proc.communicate()

        try:
            os.unlink(tmp_path)
        except OSError:
            pass

        if proc.returncode != 0:
            errors.append((cfg["label"], stderr[:400]))
        else:
            dash, player = get_images_after_timestamp(ts_before)
            match_images = []
            if dash:
                match_images.append(dash)
            if player and team_arg != "both":
                match_images.append(player)
            elif player:
                match_images.append(player)

            all_generated_images.extend(match_images)
            st.session_state.results.append({
                "label": cfg["label"],
                "images": match_images
            })

        progress_bar.progress((idx + 1) / len(valid))

    status_text.empty()
    progress_bar.empty()

    # Save to session state so they persist when the user clicks Download
    st.session_state.errors = errors
    if all_generated_images:
        st.session_state.pdf_bytes = images_to_pdf(all_generated_images)
        if len(valid) == 1:
            st.session_state.pdf_name = f"{valid[0]['label'].replace(' ', '_')}_report.pdf"
        else:
            st.session_state.pdf_name = f"scout_report_{len(valid)}_matches.pdf"
    else:
        st.session_state.pdf_bytes = None


# ─────────────────────────────────────────────
# POST-GENERATION UI (OUTSIDE BUTTON RERUN)
# ─────────────────────────────────────────────
if "results" in st.session_state and st.session_state.results:
    # ── Error report ──────────────────────────────────────────────────────
    if st.session_state.get("errors"):
        for label, err in st.session_state.errors:
            with st.expander(f"⚠️  Error — {label}"):
                st.code(err)

    # ── Success: build PDF & show previews ───────────────────────────────
    st.success(
        f"✅  Generated reports from **{len(st.session_state.results)} match(es)**."
    )

    if st.session_state.get("pdf_bytes"):
        st.download_button(
            label=f"📄  Download PDF Report",
            data=st.session_state.pdf_bytes,
            file_name=st.session_state.pdf_name,
            mime="application/pdf",
            use_container_width=True,
        )

    # ── Preview ───────────────────────────────────────────────────────
    st.markdown("<hr/>", unsafe_allow_html=True)
    st.markdown("### Preview")

    results = st.session_state.results
    if len(results) == 1:
        # Single match: just show images directly
        for img_path in results[0]["images"]:
            fname = os.path.basename(img_path)
            caption = "Player Dashboard" if "Player" in fname else "Match Dashboard"
            st.image(Image.open(img_path), caption=caption, use_container_width=True)
    else:
        # Multiple matches: one tab per match
        tab_labels = [f"Match {i+1}: {r['label']}" for i, r in enumerate(results)]
        tabs = st.tabs(tab_labels)
        for tab, r in zip(tabs, results):
            with tab:
                for img_path in r["images"]:
                    fname = os.path.basename(img_path)
                    caption = "Player Dashboard" if "Player" in fname else "Match Dashboard"
                    st.image(Image.open(img_path), caption=caption, use_container_width=True)


# ─────────────────────────────────────────────
# FOOTER NOTE
# ─────────────────────────────────────────────
st.markdown("<hr/>", unsafe_allow_html=True)
st.caption(
    "**Note for PostMatchDashboard.py maintainers:** This app passes a `--team [both|home|away]` "
    "argument to your analysis script. You can provide a WhoScored Live URL or upload saved HTML; "
    "FotMob xG/xGOT can be fetched from its match page or entered manually. See README."
)
