# MATCHANALYSIS
Hi there, I am SALMAN, data analysis enthusiast. In this repository I have uploaded the code for making Post Match report.

## Run the app

```sh
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

## Generate a report from two URLs

1. Choose **Fetch by URL** and enter the WhoScored Live URL and the FotMob URL for the same fixture.
2. Click **Generate Report(s) & Export PDF**. The app downloads and validates the WhoScored HTML, imports the two teams' xG and xGOT from FotMob, and passes the match data to the report generator.
3. To load the sources before generating a report, click **Fetch match sources**. The tactical decoder uses the same downloaded events.

The server tries a Chromium page load if WhoScored denies the direct request or the response lacks readable match events. The report requires a page with the embedded match-centre events. The app displays a provider access error if both requests are refused, and a separate error when the page has no readable event data.

Streamlit Community Cloud installs Chromium from `packages.txt`. For local development without system Chromium, install the Playwright browser with `python -m playwright install chromium`.

This project is licensed under the GNU General Public License v3.0 - see the LICENSE file for details.
