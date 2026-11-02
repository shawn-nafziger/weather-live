"""Offline tests of scripts/station_update.py and ranks.py (no network).

    python -m unittest discover tests
"""
import csv, json, sys, tempfile, unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import station_update as s  # noqa: E402
import ranks  # noqa: E402


class FakeResponse:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


class FakeSession:
    """ACIS stand-in: a full record for 'por', and a revised recent window otherwise."""
    def __init__(self, recent=None):
        self.calls, self.recent = [], recent
        self.headers = {}
    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        if json["sdate"] == "por":
            return FakeResponse({"data": [
                ["1900-01-01", "30", "10", "0.10", "1.0"],
                ["1900-01-02", "M", "M", "M", "M"],          # all missing: dropped
                ["2026-10-08", "60", "40", "T", "0.0"],       # trace of rain
                ["2026-10-27", "70", "50", "0.25A", "0.0"],   # accumulated value
            ]})
        return FakeResponse({"data": self.recent})


class Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        s.HISTORY_DIR = self.tmp
        s.time.sleep = lambda *_: None
        self.city = {"slug": "chicago", "name": "Chicago", "record_sid": "ORDthr 9"}

    def read(self):
        with (self.tmp / "chicago.csv").open() as f:
            return list(csv.DictReader(f))

    def test_first_run_downloads_whole_record(self):
        res = s.update_city(FakeSession(), self.city, date(2026, 10, 29))
        self.assertTrue(res["full_download"])
        rows = self.read()
        self.assertEqual([r["Date"] for r in rows], ["1900-01-01", "2026-10-08", "2026-10-27"])
        self.assertEqual((rows[1]["PrecipIn"], rows[1]["PrecipTrace"]), ("0.0", "True"))
        self.assertEqual((rows[2]["PrecipIn"], rows[2]["MaxTempC"]), ("0.25", "21.11"))

    def test_refresh_replaces_only_recent_days(self):
        s.update_city(FakeSession(), self.city, date(2026, 10, 29))
        recent = [["2026-10-27", "71", "50", "0.25", "0.0"],   # corrected high
                  ["2026-10-28", "76", "52", "0.00", "0.0"]]   # new day
        sess = FakeSession(recent)
        res = s.update_city(sess, self.city, date(2026, 10, 29))
        self.assertEqual(sess.calls[0]["sdate"], "2026-10-08")  # 21 days back
        rows = {r["Date"]: r for r in self.read()}
        self.assertEqual(rows["2026-10-27"]["MaxTempF"], "71.0")
        self.assertIn("2026-10-28", rows)
        self.assertIn("1900-01-01", rows)                        # old history kept
        self.assertIn("2026-10-08", rows)                        # in the window but not re-sent: kept
        self.assertEqual(res["rows_after"], 4)

    def test_stops_if_acis_returns_nothing_recent(self):
        s.update_city(FakeSession(), self.city, date(2026, 10, 29))
        before = (self.tmp / "chicago.csv").read_text()
        with self.assertRaises(RuntimeError):
            s.update_city(FakeSession([]), self.city, date(2026, 10, 29))
        self.assertEqual((self.tmp / "chicago.csv").read_text(), before)   # file untouched


class Batch(unittest.TestCase):
    """main(): both city lists, paced first downloads, and the status file."""
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        s.HISTORY_DIR = self.tmp / "history"
        s.time.sleep = lambda *_: None
        (self.tmp / "cities.json").write_text(json.dumps({"cities": [
            {"slug": "chicago", "name": "Chicago", "record_sid": "ORDthr 9"}]}))
        (self.tmp / "almanac.json").write_text(json.dumps({"cities": [
            {"slug": f"c{i}", "name": f"C{i}", "record_sid": f"X{i}thr 9"} for i in range(4)] +
            [{"slug": "chicago", "name": "Chicago again", "record_sid": "ORDthr 9"}]}))
        s.CITIES_FILE, s.ALMANAC_CITIES_FILE = self.tmp / "cities.json", self.tmp / "almanac.json"
        self.real_session = s.requests.Session
        s.requests.Session = lambda: FakeSession()

    def tearDown(self):
        s.requests.Session = self.real_session

    def test_lists_are_merged_without_duplicates(self):
        self.assertEqual([c["slug"] for c in s.load_cities()], ["chicago", "c0", "c1", "c2", "c3"])

    def test_first_downloads_are_paced_and_status_is_written(self):
        s.MAX_FULL_DOWNLOADS_PER_RUN = 2
        try:
            s.main()
            st = json.loads((s.HISTORY_DIR / "status.json").read_text())
            self.assertEqual(st["summary"], {"cities": 5, "updated": 2, "failed": 0, "waiting": 3})
            self.assertTrue(st["cities"]["c2"]["waiting"])
            s.main()                                   # next run: two more
            s.main()                                   # and the last one
            st = json.loads((s.HISTORY_DIR / "status.json").read_text())
        finally:
            s.MAX_FULL_DOWNLOADS_PER_RUN = 15
        self.assertEqual(st["summary"]["waiting"], 0)
        self.assertEqual(sorted(p.stem for p in s.HISTORY_DIR.glob("*.csv")), ["c0", "c1", "c2", "c3", "chicago"])


class RankRules(unittest.TestCase):
    def hist(self, values, md="10-28"):
        return {date(1990 + i, int(md[:2]), int(md[3:])): {"MaxTempF": str(v)} for i, v in enumerate(values)}

    def test_strictly_warmer_plus_one_with_ties(self):
        h = self.hist([70, 75, 75, 80, 60])
        r = ranks.rank(h, date(2026, 10, 28), 75)          # would-rank: today isn't in history
        self.assertEqual((r["warm_rank"], r["cool_rank"], r["years"], r["tied_years"]), (2, 3, 6, [1991, 1992]))

    def test_half_degrees_round_up(self):
        self.assertEqual((ranks.whole_f(69.5), ranks.whole_f(70.4), ranks.whole_f(-0.5)), (70, 70, 0))

    def test_record(self):
        h = self.hist([70, 75, 80])
        r = ranks.rank(h, date(2026, 10, 28), 81)
        self.assertEqual(ranks.phrase(r, date(2026, 10, 28)), "the warmest October 28 high on record (4 years since 1990)")
        r = ranks.rank(h, date(2026, 10, 28), 80)
        self.assertEqual(ranks.phrase(r, date(2026, 10, 28)), "ties the record for warmest October 28 high on record (4 years since 1990)")


if __name__ == "__main__":
    unittest.main()
