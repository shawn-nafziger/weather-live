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
    # readings: list of (iso utc, temp C or None, qc). Readings at :51 are hourly METARs
    # (tenths, with a T-group); the rest are 5-minute readings (whole degrees, no METAR).
    def raw(t, c):
        if t[14:16] != "51" or c is None:
            return ""
        sign = "1" if c < 0 else "0"
        return f"KORD 291951Z 21010KT 10SM CLR {c:.0f}/05 A3000 RMK AO2 T{sign}{abs(round(c*10)):03d}0050"
    return {"features": [{"properties": {
        "timestamp": t, "rawMessage": raw(t, c), "temperature": {"unitCode": "wmoUnit:degC", "value": c, "qualityControl": q},
        "dewpoint": {"unitCode": "wmoUnit:degC", "value": 5.0}, "relativeHumidity": {"value": 40.0},
        "windDirection": {"value": 200}, "windSpeed": {"value": 15.0}, "windGust": {"value": None},
        "barometricPressure": {"value": 101500}, "precipitationLastHour": {"value": None},
        "maxTemperatureLast24Hours": {"value": None}, "minTemperatureLast24Hours": {"value": None},
        "textDescription": "Clear"}} for t, c, q in reversed(readings)]}

READINGS = [  # Chicago local = UTC-5 (CDT). Local midnight Oct 29 = 05:00Z.
    ("2026-10-29T03:51:00+00:00", 25.0, "V"),   # 10:51 pm Oct 28 local: yesterday, must be ignored
    ("2026-10-29T05:51:00+00:00", 8.3, "V"),    # 12:51 am: today's low
    ("2026-10-29T14:51:00+00:00", 15.0, "V"),
    ("2026-10-29T19:51:00+00:00", 21.1, "V"),   # today's high (70 F) among hourly reports
    ("2026-10-29T20:30:00+00:00", 23.0, "V"),   # 5-minute reading, whole degrees: a peak between reports
    ("2026-10-29T20:51:00+00:00", 30.0, "X"),   # failed quality control: ignored
    ("2026-10-29T21:40:00+00:00", 20.0, "V"),   # latest usable reading (5-minute)
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
             "time_zone": "America/Chicago", "history": "same",
             "record_sid": "ORDthr 9", "record_name": "Chicago", "rank_today": True},
            {"slug": "miami", "name": "Miami", "state": "FL", "location_key": 4, "station": "KMIA",
             "time_zone": "America/New_York", "history": "same",
             "record_sid": "MIAthr 9", "record_name": "Miami", "rank_today": False}]}
        (self.tmp / "cities.json").write_text(json.dumps(cfg))
        n.CITIES_FILE = self.tmp / "cities.json"
        n.FORECAST_DIR = self.tmp / "archive/forecasts"
        n.OBSERVATION_DIR = self.tmp / "archive/observations"
        n.LIVE_DIR = self.tmp / "live"
        n.HISTORY_DIR = self.tmp / "history"
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
        self.assertEqual(len(o29), 16)                      # 8 readings x 2 stations
        self.assertEqual([r["ReportType"] for r in o29[:2]], ["METAR", "METAR"])
        self.assertIn("T0211", [r["RawMessage"] for r in o29 if r["TimestampUTC"].endswith("19:51:00Z")][0])
        live = json.loads((self.tmp / "live/chicago.json").read_text())
        t = live["today_so_far"]
        self.assertEqual((t["high_c"], t["high_f"], t["low_c"]), (21.1, 70.0, 8.3))
        self.assertEqual(t["readings"], 3)                  # hourly reports only; yesterday, QC-failed, missing excluded
        self.assertFalse(t["approximate"])
        self.assertEqual(t["peak_between_reports"]["at_least_f"], 72.5)   # 23 C rounded means at least 22.5 C
        self.assertNotIn("dip_between_reports", t)
        self.assertEqual((live["now"]["temp_c"], live["now"]["precision"]), (20.0, "nearest whole °C"))  # newest usable
        self.assertEqual(live["hourly"]["hours"][0]["start"], "2026-10-29T22:00:00+00:00")  # past hours dropped
        # Miami is UTC-4 (EDT): local midnight = 04:00Z, so the 03:51Z reading is still yesterday.
        self.assertEqual(json.loads((self.tmp / "live/miami.json").read_text())["today_so_far"]["readings"], 3)

    def test_rerun_adds_no_duplicates_and_new_issue_is_kept(self):
        n.get_json = FakeAPI(); n.main(); n.main()
        self.assertEqual(len(self.rows(self.tmp / "archive/forecasts/2026/10/2026-10-29.csv")), 8)
        self.assertEqual(len(self.rows(self.tmp / "archive/observations/2026/10/2026-10-29.csv")), 16)
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

    def test_today_so_far_rank_is_warm_side_only(self):
        h = self.tmp / "history"; h.mkdir()
        def write(base):
            lines = ["Date,MaxTempF,MinTempF"] + [f"{y}-10-29,{base + i},30" for i, y in enumerate(range(1996, 2026))]
            (h / "chicago.csv").write_text("\n".join(lines) + "\n")
        write(80)                     # every year 80-109 F: today's 70 F so far would be "coolest"
        n.get_json = FakeAPI(); n.main()
        rec = json.loads((self.tmp / "live/chicago.json").read_text())["record"]
        self.assertNotIn("today_high_so_far", rec)
        write(40)                     # every year 40-69 F: 70 F is already a record
        n.main()
        th = json.loads((self.tmp / "live/chicago.json").read_text())["record"]["today_high_so_far"]
        self.assertEqual(th["phrase"], "already the warmest October 29 high on record (31 years since 1996)")

    def test_old_day_file_keeps_its_columns(self):
        # A file started before ReportType existed keeps its header; no ragged rows.
        old = self.tmp / "archive/observations/2026/10/2026-10-29.csv"
        old.parent.mkdir(parents=True)
        cols = [c for c in n.OBSERVATION_COLUMNS if c not in ("ReportType", "Precise", "RawMessage")]
        old.write_text(",".join(cols) + "\n")
        n.get_json = FakeAPI(); n.main()
        rows = self.rows(old)
        self.assertEqual(list(rows[0].keys()), cols)
        self.assertTrue(all(None not in r for r in rows))

    def test_ranks_from_history(self):
        # 30 years of Oct 28/29/30: highs 50..79 F. Yesterday (2026-10-28) = 78 F.
        h = self.tmp / "history"; h.mkdir()
        lines = ["Date,MaxTempF,MinTempF"]
        for i, y in enumerate(range(1996, 2026)):
            for md in ("10-28", "10-29", "10-30"):
                lines.append(f"{y}-{md},{50 + i},{30 + i}")
        lines.append("2026-10-28,78,40")
        (h / "chicago.csv").write_text("\n".join(lines) + "\n")
        (h / "miami.csv").write_text("\n".join(lines) + "\n")
        n.get_json = FakeAPI(); n.main()
        rec = json.loads((self.tmp / "live/chicago.json").read_text())["record"]
        lf = rec["latest_final"]
        self.assertTrue(lf["is_yesterday"])
        self.assertEqual((lf["high_f"], lf["high_rank"]["warm_rank"], lf["high_rank"]["years"]), (78, 2, 31))
        self.assertEqual(lf["high_rank"]["tied_years"], [2024])
        self.assertEqual(lf["high_rank"]["phrase"], "2nd warmest October 28 high (tied) of 31 since 1996, warmest since 2025")  # 2025 (79 F) was warmer
        # Today's high so far, 21.1 C = 70.0 F -> 70 F: 2017..2025 (71..79) are warmer; 2016 ties.
        # 70 F is typical, so no claim yet.
        self.assertNotIn("today_high_so_far", rec)
        # Forecast daytime periods would-rank; the 2 daytime fixture periods start Oct 29.
        self.assertEqual([f["high_f"] for f in rec["forecast_highs"]], [65, 55])
        self.assertEqual(rec["forecast_highs"][1]["would_rank"]["phrase"],
                         "6th coolest October 29 high (tied) of 31 since 1996, coolest since 2001")  # 2001 also 55 F
        # Miami's today readings come from another station: yesterday only, no today/forecast ranks.
        mia = json.loads((self.tmp / "live/miami.json").read_text())["record"]
        self.assertIn("latest_final", mia); self.assertNotIn("today_high_so_far", mia); self.assertIn("note", mia)


if __name__ == "__main__":
    unittest.main()
