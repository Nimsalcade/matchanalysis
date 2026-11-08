# MATCHANALYSIS
Hi there, I am SALMAN, data analysis enthusiast. In this repository I have uploaded the code for making Post Match report.

## Run the app

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Generate a report from two URLs

1. Choose **Fetch by URL** and enter the WhoScored Live URL and the FotMob URL for the same fixture.
2. Click **Fetch match sources**. WhoScored supplies the event HTML. FotMob fills the two teams' xG and xGOT.
3. If the server cannot load WhoScored, install the [Chrome companion](browser_extension/README.md) once and click **Fetch WhoScored in this browser**. It opens the match in a regular browser tab and sends the embedded event HTML directly to the app.
4. Once the event teams and xG/xGOT appear, click **Generate Report(s) & Export PDF**. The tactical decoder uses the same event data.

The full report requires a WhoScored Live page with embedded match-centre events. A fixture may have a public Summary page with lineups and goals and no event feed. The app shows missing events separately from an HTTP access error. Saved WhoScored HTML upload and manual entry of verified xG/xGOT are also supported.

Optional server rendering for successful HTTP requests that need JavaScript requires `python -m pip install playwright` and `python -m playwright install chromium` on the server. The Chrome companion runs in the user's browser.

This project is licensed under the GNU General Public License v3.0 - see the LICENSE file for details.
