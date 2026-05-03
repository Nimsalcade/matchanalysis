import streamlit as st
import os
import subprocess
import glob
from PIL import Image
import tempfile
import io
import sys
import time
import re

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


def _parse_score_title(title_text: str) -> tuple:
    """Try to extract (home, away) from a string like
    'Manchester United 2-0 Manchester City - Premier League …'.
    Returns ("", "") on failure.
    """
    m = re.match(r'^(.+?)\s+(\d+\s*-\s*\d+)\s+(.+?)\s*-\s*.+$', title_text)
    if m:
        home, away = m.group(1).strip(), m.group(3).strip()
        if home and away:
            return home, away
    return "", ""


def extract_team_names(html_bytes: bytes, filename: str = "") -> tuple:
    """Extract (home_team, away_team) from a WhoScored HTML file.

    Returns a tuple of two strings. If detection fails, returns ("", "").

    Detection strategy (ordered by reliability):
      0. **Filename** — saved pages are named
         ``"Manchester United 2-0 Manchester City - Premier League … Live.html"``.
      1. ``<meta name="title" …>`` — searched up to 200 KB (saved pages inline
         huge JS before <head> meta tags).
      2. "Home vs Away" text pattern in the first 200 KB (pre-match pages).
      3. ``<a class="team-link" …>Team</a>`` anchors — up to 1 MB.
    """

    # ── Strategy 0: filename ──────────────────────────────────────────
    if filename:
        # Strip extension and path
        import os
        base = os.path.splitext(os.path.basename(filename))[0]
        home, away = _parse_score_title(base)
        if home and away:
            return home, away
        # Also try "Home vs Away" in filename
        m = re.match(r'^(.+?)\s+vs\.?\s+(.+?)\s*[-–]', base, re.IGNORECASE)
        if m:
            return m.group(1).strip(), m.group(2).strip()

    text = html_bytes.decode("utf-8", errors="replace")
    # Saved WhoScored pages inline massive JS, pushing meta tags to ~55 KB+
    search_chunk = text[:204800]   # 200 KB

    # ── Strategy 1: <meta name="title" content="…"> ──────────────────
    meta_title = re.search(
        r'<meta\s+name=["\']title["\']\s+content=["\']([^"\']+)["\']',
        search_chunk,
        re.IGNORECASE,
    )
    if meta_title:
        home, away = _parse_score_title(meta_title.group(1).strip())
        if home and away:
            return home, away

    # ── Strategy 2: "Home vs Away" text in head area ──────────────────
    vs_pattern = re.compile(
        r'([^"<>]{2,40})\s+vs\.?\s+([^"<>]{2,40})',
        re.IGNORECASE,
    )
    for m in vs_pattern.finditer(search_chunk):
        home = m.group(1).strip()
        away = m.group(2).strip()
        if re.search(r'[A-Za-z]', home) and re.search(r'[A-Za-z]', away):
            return home, away

    # ── Strategy 3: team-link <a> anchors (up to 1 MB) ────────────────
    team_link_pattern = re.compile(
        r'<a[^>]+class="[^"]*team-link[^"]*"[^>]*>\s*([^<]+?)\s*</a>',
        re.IGNORECASE,
    )
    team_links = team_link_pattern.findall(text[:1048576])   # 1 MB
    if len(team_links) >= 2:
        return team_links[0].strip(), team_links[1].strip()

    return "", ""


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
             "Team names appear automatically once you upload an HTML file.",
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
            st.session_state.detected_home = ""
            st.session_state.detected_away = ""
            st.rerun()

    st.markdown("<hr/>", unsafe_allow_html=True)
    st.caption(
        "**Tip:** Upload a match HTML first — the app detects team names "
        "and populates the radio options above so you can pick which team to scout."
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
match_inputs = []   # list of dicts per match

for i in range(st.session_state.match_count):
    st.markdown(f"<div class='match-label'>MATCH {i + 1}</div>", unsafe_allow_html=True)

    with st.container():
        col_file, col_del = st.columns([8, 1])

        with col_file:
            uploaded = st.file_uploader(
                f"WhoScored HTML — Match {i + 1}",
                type=["html"],
                key=f"file_{i}",
                label_visibility="collapsed",
            )
        with col_del:
            if st.session_state.match_count > 1:
                if st.button("✕", key=f"del_{i}", help="Remove this match"):
                    st.session_state.match_count -= 1
                    st.rerun()

        # ── Auto-detect team names from uploaded file ─────────────────
        home_name, away_name = "", ""
        if uploaded is not None:
            html_bytes = uploaded.getvalue()
            home_name, away_name = extract_team_names(html_bytes, uploaded.name)
            # Store the first successful detection for the sidebar radio
            if home_name and away_name:
                if not st.session_state.detected_home:
                    st.session_state.detected_home = home_name
                    st.session_state.detected_away = away_name
                    st.rerun()   # rerun so sidebar picks up the names
                if home_name and away_name:
                    st.caption(f"🏟️  Detected: **{home_name}** (H) vs **{away_name}** (A)")
            else:
                st.warning("⚠️ Could not detect team names from this file.")

        # Optional match label — auto-populate if names detected
        default_label = (
            f"{home_name} vs {away_name}" if home_name and away_name
            else f"Match {i + 1}"
        )
        match_label = st.text_input(
            "Match label (optional)",
            value=default_label,
            key=f"label_{i}",
            placeholder="e.g. Arsenal vs Chelsea – GW28",
        )

        # ── xG / xGOT inputs (adaptive to team_focus) ────────────────
        if team_focus == "Both Teams":
            # Show all 4 inputs
            c1, c2, c3, c4 = st.columns(4)
            with c1:
                home_xg = st.number_input(
                    f"{home_name or 'Home'} xG", min_value=0.0, max_value=10.0,
                    value=0.89, step=0.01, format="%.2f", key=f"hxg_{i}"
                )
            with c2:
                home_xgot = st.number_input(
                    f"{home_name or 'Home'} xGOT", min_value=0.0, max_value=10.0,
                    value=0.56, step=0.01, format="%.2f", key=f"hxgot_{i}"
                )
            with c3:
                away_xg = st.number_input(
                    f"{away_name or 'Away'} xG", min_value=0.0, max_value=10.0,
                    value=1.09, step=0.01, format="%.2f", key=f"axg_{i}"
                )
            with c4:
                away_xgot = st.number_input(
                    f"{away_name or 'Away'} xGOT", min_value=0.0, max_value=10.0,
                    value=1.13, step=0.01, format="%.2f", key=f"axgot_{i}"
                )
        else:
            # Single team — only 2 inputs, labelled with the selected team
            focus_label = (
                st.session_state.detected_home if team_focus_side == "home"
                else st.session_state.detected_away
            ) or ("Home" if team_focus_side == "home" else "Away")
            c1, c2 = st.columns(2)
            with c1:
                focus_xg = st.number_input(
                    f"{focus_label} xG", min_value=0.0, max_value=10.0,
                    value=0.89, step=0.01, format="%.2f", key=f"fxg_{i}"
                )
            with c2:
                focus_xgot = st.number_input(
                    f"{focus_label} xGOT", min_value=0.0, max_value=10.0,
                    value=0.56, step=0.01, format="%.2f", key=f"fxgot_{i}"
                )
            # Map to home/away based on which side is focused
            if team_focus_side == "home":
                home_xg, home_xgot = focus_xg, focus_xgot
                away_xg, away_xgot = 0.0, 0.0
            else:
                home_xg, home_xgot = 0.0, 0.0
                away_xg, away_xgot = focus_xg, focus_xgot

        match_inputs.append({
            "file": uploaded,
            "label": match_label,
            "home_xg": home_xg,
            "home_xgot": home_xgot,
            "away_xg": away_xg,
            "away_xgot": away_xgot,
        })

    st.markdown("<hr/>", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# GENERATE BUTTON
# ─────────────────────────────────────────────
generate_btn = st.button(
    f"🚀  Generate {st.session_state.match_count} Report(s) & Export PDF",
    type="primary",
    use_container_width=True,
)

if generate_btn:
    valid = [(m["file"], m) for m in match_inputs if m["file"] is not None]

    if not valid:
        st.error("⚠️  Please upload at least one WhoScored HTML file.")
        st.stop()

    all_generated_images = []   # flat list of image paths, in order
    errors = []

    progress_bar = st.progress(0)
    status_text = st.empty()

    for idx, (html_file, cfg) in enumerate(valid):
        html_bytes = html_file.getvalue()

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
            st.session_state.pdf_name = f"{valid[0][1]['label'].replace(' ', '_')}_report.pdf"
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
    "argument to your analysis script. Team names are auto-detected from the HTML and the user "
    "selects which team to focus on via a radio button. See README for details."
)
