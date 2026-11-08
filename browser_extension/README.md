# MatchAnalysis Browser Fetch

1. In desktop Chrome, open `chrome://extensions` and enable **Developer mode**.
2. Choose **Load unpacked** and select this `browser_extension` directory.
3. Reload MatchAnalysis at `https://matchareport.streamlit.app/` or `http://localhost:8501/`.
4. Paste the match URLs, choose **Fetch match sources**, then choose **Fetch WhoScored in this browser** when the app offers it.

Chrome opens the requested WhoScored Live match in a background tab. The extension passes the embedded match event script to MatchAnalysis and closes the tab on success. If a match page lacks its event feed or fails to load, the tab stays open for inspection. FotMob continues to load through the app.
