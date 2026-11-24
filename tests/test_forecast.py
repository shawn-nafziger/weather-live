"""Offline tests of the 7-day forecast strip (almanac/forecast.py). No network: the forecasts are fixtures in
tests/fixtures/forecasts/, shaped like what scripts/forecast_pull.py saves (values made up)."""
import json, re, shutil, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "almanac"))
import build as a  # noqa: E402
import carry_over  # noqa: E402
import forecast  # noqa: E402

FIX = REPO / "tests" / "fixtures" / "forecasts"
NOW = datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc)   # 3.3 hours after the fixtures were issued


def strip(slug, now=NOW, fc=None):
    h = a.load(slug)
    return forecast.strip_html(fc or forecast.load(FIX, slug), h, a.same_date_rank, now=now, city_name=a.CITIES[slug]["name"])


def text(html_):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", html_, flags=re.S)))


class Test(unittest.TestCase):
    def test_seven_days_with_high_low_and_issue_time(self):
        s = strip("eau-claire")
        self.assertEqual(s.count('<li class="fc-day'), 7)
        t = text(s)
        self.assertIn("issued Nov 21, 5:40 AM CST", t)          # 11:40 UTC in Central time
        self.assertIn("Today Nov 21 66° 41°", t)
        self.assertEqual(s.count("Less certain"), 2)              # days 6 and 7

    def test_every_note_is_true_against_the_record(self):
        h = a.load("eau-claire")
        fc = forecast.load(FIX, "eau-claire")
        for row in forecast.days(fc):
            d = row["date"]
            md = d.strftime("%m-%d")
            hi_vals = {int(y): int(v) for y, v, m in zip(h["Y"], h["Hi"], h["MD"]) if m == md and v == v}
            if row["high"] is not None and hi_vals:
                warmer = sum(1 for v in hi_vals.values() if v > row["high"])
                note = forecast.notable_line(row, {md: hi_vals}, {}, a.same_date_rank, d.year)
                if note and "warmest" in note["text"] and note["kind"] == "high":
                    self.assertIn(f"{forecast._ordinal(warmer + 1)} warmest", note["text"])
                    self.assertIn(f"Nov {d.day} high in {len(hi_vals) + 1} years", note["text"])

    def test_record_wording(self):
        h = a.load("eau-claire")
        lows = {int(y): int(v) for y, v, m in zip(h["Y"], h["Lo"], h["MD"]) if m == "11-25" and v == v}
        rec = min(lows.values())
        row = {"date": datetime(2026, 11, 24).date(), "high": None, "low": rec, "precip": 0, "short": "", "label": "Tue"}
        note = forecast.notable_line(row, {}, {"11-25": lows}, a.same_date_rank, 2026)
        self.assertTrue(note["record"])
        self.assertTrue(note["text"].startswith("Low that night would tie the Nov 25 record low"), note["text"])
        row["low"] = rec - 1
        note = forecast.notable_line(row, {}, {"11-25": lows}, a.same_date_rank, 2026)
        self.assertTrue(note["text"].startswith("Low that night would beat the Nov 25 record low"), note["text"])

    def test_stale_or_missing_forecast_shows_no_strip(self):
        self.assertEqual(strip("eau-claire", now=datetime(2026, 11, 23, 12, tzinfo=timezone.utc)), "")
        self.assertEqual(forecast.strip_html(None, a.load("eau-claire"), a.same_date_rank), "")

    def test_evening_forecast_starts_with_tonight(self):
        fc = json.loads((FIX / "eau-claire.json").read_text())
        fc["periods"] = fc["periods"][1:]      # issued in the evening: the first period is "Tonight"
        rows = forecast.days(fc)
        self.assertIsNone(rows[0]["high"])
        self.assertIn("<b>Tonight</b>", strip("eau-claire", fc=fc))

    def test_page_has_strip_and_carry_over_drops_it(self):
        tmp = Path(tempfile.mkdtemp())
        keep = forecast.MAX_AGE_HOURS
        forecast.MAX_AGE_HOURS = 10 ** 6       # the fixture is from Nov 21, 2026: keep it "fresh" for this test
        try:
            sys.argv = ["build.py", "eau-claire", "--out", str(tmp / "site"), "--forecasts", str(FIX)]
            self.assertEqual(a.main(), 0)
            page = tmp / "site" / "almanac" / "eau-claire" / "index.html"
            html_ = page.read_text(encoding="utf-8")
            self.assertIn("Eau Claire 7-day forecast", html_)
            self.assertNotIn("{{", html_)
            carry_over._drop_forecast(page)         # what a carried-over page goes through
            after = page.read_text(encoding="utf-8")
            self.assertNotIn('class="forecast"', after)
            self.assertIn("Eau Claire", after)
        finally:
            forecast.MAX_AGE_HOURS = keep
            shutil.rmtree(tmp)


class TestIcons(unittest.TestCase):
    def test_nws_words_pick_the_right_icon(self):
        cases = {"Sunny": "sun", "Mostly Sunny": "partly", "Partly Cloudy": "partly", "Mostly Cloudy": "cloud",
                 "Cloudy": "cloud", "Chance Rain Showers": "rain", "Slight Chance Drizzle": "rain",
                 "Chance Showers And Thunderstorms": "storm", "Light Snow Likely": "snow",
                 "Rain And Snow": "snow", "Freezing Rain": "mix", "Patchy Fog": "fog", "Breezy": "wind",
                 "Areas Of Smoke": "fog", "Something New": None}
        for words, want in cases.items():
            self.assertEqual(forecast.icon_name(words), want, words)
        self.assertEqual(forecast.icon_name("Mostly Clear", night=True), "partly-night")
        self.assertEqual(forecast.icon_name("Clear", night=True), "moon")
        self.assertEqual(forecast.icon("Something New"), "")


class FakeSession:
    """Answers like api.weather.gov (trimmed), so the fetch can be tested offline."""
    class R:
        def __init__(self, body): self.body, self.status_code = body, 200
        def raise_for_status(self): pass
        def json(self): return self.body

    def get(self, url, timeout=None):
        if "/points/" in url:
            return self.R({"properties": {"forecast": "https://api.weather.gov/gridpoints/MPX/1,1/forecast",
                                          "timeZone": "America/Chicago", "gridId": "MPX", "gridX": 1, "gridY": 1}})
        return self.R({"properties": {"generatedAt": "2026-11-21T11:50:00+00:00", "updateTime": "2026-11-21T11:40:00+00:00",
            "periods": [{"number": 1, "name": "Today", "startTime": "2026-11-21T06:00:00-06:00", "isDaytime": True,
                         "temperature": 66, "temperatureUnit": "F", "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": None},
                         "shortForecast": "Sunny"},
                        {"number": 2, "name": "Tonight", "startTime": "2026-11-21T18:00:00-06:00", "isDaytime": False,
                         "temperature": 5, "temperatureUnit": "C", "probabilityOfPrecipitation": {"value": 20},
                         "shortForecast": "Mostly Clear"}]}})


class TestFetch(unittest.TestCase):
    def test_nws_answer_becomes_a_forecast_file(self):
        fc = forecast.fetch_one(FakeSession(), "eau-claire", 44.8658, -91.4843)
        self.assertEqual(fc["issued_utc"], "2026-11-21T11:40:00+00:00")   # updateTime, not generatedAt
        self.assertEqual(fc["time_zone"], "America/Chicago")
        self.assertEqual([p["temp_f"] for p in fc["periods"]], [66, 41])  # 5 °C converted to °F
        self.assertEqual([p["precip_pct"] for p in fc["periods"]], [None, 20])
        rows = forecast.days(fc)
        self.assertEqual((rows[0]["high"], rows[0]["low"], rows[0]["precip"]), (66, 41, 20))


if __name__ == "__main__":
    unittest.main()
