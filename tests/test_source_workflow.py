from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

import match_sources
from match_sources import FotMobStats, SourceError, fetch_fotmob, fetch_whoscored


APP = Path(__file__).resolve().parents[1] / "app.py"
WHOSCORED = "https://www.whoscored.com/matches/2031670/live/bayern-munich-magdeburg"
FOTMOB = "https://www.fotmob.com/matches/magdeburg-vs-bayern-munchen/2okvcp#6162738:tab=stats"
MATCH_HTML = b'''<html><script>
require.config.params["args"] = {
  matchId: 2031670,
  matchCentreData: {
    "home": {"name": "Bayern Munich", "teamId": 1},
    "away": {"name": "Magdeburg", "teamId": 2},
    "events": [{"id": 1, "type": {"displayName": "Pass"}}]
  }
};
</script></html>'''


class SourceWorkflowTests(TestCase):
    def load_blocked_match(self, fotmob_away="Magdeburg"):
        stats = FotMobStats(3.98, 0.86, 4.02, 0.64, FOTMOB, "embedded JSON",
                            "Bayern München", fotmob_away)
        app = AppTest.from_file(APP, default_timeout=15).run()
        app.text_input(key="whoscored_url_0").set_value(WHOSCORED)
        app.text_input(key="fotmob_url_0").set_value(FOTMOB)
        with (
            patch.object(match_sources, "fetch_whoscored", side_effect=SourceError("HTTP 403")),
            patch.object(match_sources, "fetch_fotmob", return_value=stats),
        ):
            app.button(key="fetch_0").click().run()
        self.assertFalse(app.exception)
        return app

    def test_blocked_fetch_stays_visible_and_upload_retains_stats(self):
        app = self.load_blocked_match()
        self.assertTrue(any("HTTP 403" in item.value for item in app.error))
        self.assertEqual(app.file_uploader[0].label, "WhoScored saved HTML")

        generate = next(button for button in app.button if "Generate" in button.label)
        if generate.proto.disabled:
            app.run()
        else:
            generate.click().run()
        self.assertTrue(any("HTTP 403" in item.value for item in app.error))

        with (
            patch.object(match_sources, "fetch_whoscored") as fetch_events,
            patch.object(match_sources, "fetch_fotmob") as fetch_stats,
        ):
            app.file_uploader[0].upload("match.html", MATCH_HTML, "text/html").run()
            fetch_events.assert_not_called()
            fetch_stats.assert_not_called()

        self.assertFalse(app.exception)
        self.assertTrue(any("Bayern Munich" in item.value and "Magdeburg" in item.value
                            for item in app.caption))
        self.assertEqual(app.text_input(key="hxg_0").value, "3.98")
        self.assertEqual(app.text_input(key="hxgot_0").value, "4.02")
        self.assertEqual(app.text_input(key="axg_0").value, "0.86")
        self.assertEqual(app.text_input(key="axgot_0").value, "0.64")

    def test_upload_checks_already_imported_fotmob_stats(self):
        app = self.load_blocked_match(fotmob_away="Darmstadt")
        app.file_uploader[0].upload("match.html", MATCH_HTML, "text/html").run()

        self.assertFalse(app.exception)
        self.assertTrue(any("FotMob" in item.value and "different" in item.value
                            for item in app.error))
        generate = next(button for button in app.button if "Generate" in button.label)
        if not generate.proto.disabled:
            with patch("subprocess.Popen") as render:
                generate.click().run()
                render.assert_not_called()
        self.assertFalse(app.exception)

    def test_browser_capture_is_bound_to_the_active_request(self):
        app = self.load_blocked_match()
        capture = {"url": WHOSCORED, "request_id": "stale", "html": MATCH_HTML.decode()}
        with patch("streamlit.components.v1.declare_component", return_value=Mock(return_value=capture)):
            app.run()
            self.assertFalse(app.session_state["match_download_0"].get("html"))

            capture["request_id"] = app.session_state["match_download_0"]["request_id"]
            app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state["match_download_0"]["source"], "browser")
        self.assertEqual(app.session_state["match_download_0"]["html"], MATCH_HTML)
        self.assertEqual(app.text_input(key="hxgot_0").value, "4.02")


class BrowserFallbackTests(TestCase):
    def test_browser_rendering_does_not_retry_http_access_blocks(self):
        for fetch, url in ((fetch_whoscored, WHOSCORED), (fetch_fotmob, FOTMOB)):
            for status in (401, 403, 429):
                with self.subTest(provider=fetch.__name__, status=status):
                    session = Mock()
                    session.get.return_value = Mock(status_code=status, headers={})
                    with patch.object(match_sources, "_browser_html") as browser:
                        with self.assertRaisesRegex(SourceError, f"HTTP {status}"):
                            fetch(url, browser_fallback=True, session=session)
                        browser.assert_not_called()
