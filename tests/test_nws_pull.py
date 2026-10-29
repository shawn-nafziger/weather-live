"""Offline test of scripts/nws_pull.py with made-up NWS responses (no network).

    python -m unittest discover tests
"""
import csv, json, sys, tempfile, unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import nws_pull as n  # noqa: E402

def forecast(update):
    periods = []
    names = ["This Afternoon", "Tonight", "Friday", "Friday Night"]
    for i, name in enumerate(names, 1):
        periods.append({"number": i, "name": name, "startTime": f"2026-10-29T{12+i:02d}:00:00-05:00",
                        "endTime": f"2026-10-29T{13+i:02d}:00:00-05:00", "isDaytime": i % 2 == 1,
                        "temperature": 70 - i * 5, "temperatureUnit": "F",
                        "probabilityOfPrecipitation": {"unitCode": "wmoUnit:percent", "value": 10 * i},
                        "windSpeed": "10 mph", "windDirection": "SW", "shortForecast": "Sunny"})
    return {"properties": {"updateTime": update, "generatedAt": "2026-10-29T20:05:00+00:00", "periods": periods}}

def hourly():
    return {"properties": {"updateTime": "2026-10-29T19:00:00+00:00", "periods": [
        {"startTime": f"2026-10-29T{h:02d}:00:00+00:00", "temperature": 60 + h - 18, "temperatureUnit": "F",
         "probabilityOfPrecipitation": {"value": 5}, "shortForecast": "Clear"} for h in range(18, 24)]}}

def obs(readings):
    # readings: list of (iso utc, temp C or None, qc)
    return {"features": [{"properties": {
        "timestamp": t, "temperature": {"unitCode": "wmoUnit:degC", "value": c, "qualityControl": q},
        "dewpoint": {"unitCode": "wmoUnit:degC", "value": 5.0}, "relativeHumidity": {"value": 40.0},
        "windDirection": {"value": 200}, "windSpeed": {"value": 15.0}, "windGust": {"value": None},
        "barometricPressure": {"value": 101500}, "precipitationLastHour": {"value": None},
        "maxTemperatureLast24Hours": {"value": None}, "minTemperatureLast24Hours": {"value": None},
        "textDescription": "Clear"}} for t, c, q in reversed(readings)]}

READINGS = [  # Chicago local = UTC-5 (CDT). Local midnight Oct 29 = 05:00Z.
    ("2026-10-29T03:51:00+00:00", 25.0, "V"),   # 10:51 pm Oct 28 local: yesterday, must be ignored
    ("2026-10-29T05:51:00+00:00", 8.3, "V"),    # 12:51 am: today's low
    ("2026-10-29T14:51:00+00:00", 15.0, "V"),
    ("2026-10-29T19:51:00+00:00", 21.1, "V"),   # today's high (70 F)
    ("2026-10-29T20:51:00+00:00", 30.0, "X"),   # failed quality control: ignored
    ("2026-10-29T21:51:00+00:00", None, "Z"),   # missing
]

class FakeAPI:
    def __init__(self, update="2026-10-29T19:30:00+00:00", fail_station=None):
        self.update, self.fail_station = update, fail_station
    def __call__(self, session, url, params=None):
        if self.fail_station and self.fail_station in url:
            raise RuntimeError("HTTP 503 from " + url)
        if url.endswith("/observations"):
            return obs(READINGS)
        if "/stations/" in url:
            return {"geometry": {"coordinates": [-87.9048, 41.9786]}, "properties": {"name": "Test Station"}}
        if "/points/" in url:
            return {"properties": {"gridId": "LOT", "gridX": 65, "gridY": 76,
                                   "forecast": "F/forecast", "forecastHourly": "F/forecast/hourly"}}
        if url.endswith("/hourly"):
            return hourly()
        if url.endswith("/forecast"):
            return forecast(self.update)
        raise AssertionError(url)

class Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        cfg = {"cities": [
            {"slug": "chicago", "name": "Chicago", "state": "IL", "location_key": 2, "station": "KORD",
             "time_zone": "America/Chicago", "history": "same"},
            {"slug": "miami", "name": "Miami", "state": "FL", "location_key": 4, "station": "KMIA",
             "time_zone": "America/New_York", "history": "same"}]}
        (self.tmp / "cities.json").write_text(json.dumps(cfg))
        n.CITIES_FILE = self.tmp / "cities.json"
        n.FORECAST_DIR = self.tmp / "archive/forecasts"
        n.OBSERVATION_DIR = self.tmp / "archive/observations"
        n.LIVE_DIR = self.tmp / "live"
        n.utc_now = lambda: datetime(2026, 10, 29, 22, 17, tzinfo=timezone.utc)

    def rows(self, path):
        with path.open() as f:
            return list(csv.DictReader(f))

    def test_full_run(self):
        n.get_json = FakeAPI()
        self.assertEqual(n.main(), 0)
        f = self.rows(self.tmp / "archive/forecasts/2026/10/2026-10-29.csv")
        self.assertEqual(len(f), 8)                         # 4 periods x 2 cities
        self.assertEqual(f[0]["TempF"], "65"); self.assertEqual(f[0]["TempC"], "18.33")
        self.assertEqual(f[0]["IssuedUTC"], "2026-10-29T19:30:00Z")
        o29 = self.rows(self.tmp / "archive/observations/2026/10/2026-10-29.csv")
        self.assertEqual(len(o29), 12)                      # 6 readings x 2 stations
        live = json.loads((self.tmp / "live/chicago.json").read_text())
        t = live["today_so_far"]
        self.assertEqual((t["high_c"], t["high_f"], t["low_c"]), (21.1, 70.0, 8.3))
        self.assertEqual(t["readings"], 3)                  # yesterday, QC-failed and missing excluded
        self.assertEqual(live["now"]["temp_c"], 21.1)                   # newest reading that passed quality control
        self.assertEqual(live["hourly"]["hours"][0]["start"], "2026-10-29T22:00:00+00:00")  # past hours dropped
        # Miami is UTC-4 (EDT): local midnight = 04:00Z, so the 03:51Z reading is still yesterday.
        self.assertEqual(json.loads((self.tmp / "live/miami.json").read_text())["today_so_far"]["readings"], 3)

    def test_rerun_adds_no_duplicates_and_new_issue_is_kept(self):
        n.get_json = FakeAPI(); n.main(); n.main()
        self.assertEqual(len(self.rows(self.tmp / "archive/forecasts/2026/10/2026-10-29.csv")), 8)
        self.assertEqual(len(self.rows(self.tmp / "archive/observations/2026/10/2026-10-29.csv")), 12)
        n.get_json = FakeAPI(update="2026-10-29T21:45:00+00:00"); n.main()
        f = self.rows(self.tmp / "archive/forecasts/2026/10/2026-10-29.csv")
        self.assertEqual(len(f), 16)                        # the new issue is added; the old one stays
        self.assertEqual(sorted({r["IssuedUTC"] for r in f}), ["2026-10-29T19:30:00Z", "2026-10-29T21:45:00Z"])

    def test_one_city_failing_doesnt_stop_others(self):
        n.get_json = FakeAPI(fail_station="KMIA")
        self.assertEqual(n.main(), 0)
        status = json.loads((self.tmp / "live/status.json").read_text())
        self.assertTrue(status["cities"]["chicago"]["ok"]); self.assertFalse(status["cities"]["miami"]["ok"])
        self.assertIn("503", status["cities"]["miami"]["error"])

    def test_all_failing_fails_the_run(self):
        n.get_json = FakeAPI(fail_station="/stations/K")
        self.assertEqual(n.main(), 1)

if __name__ == "__main__":
    unittest.main()
