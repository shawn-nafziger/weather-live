"""Offline tests of scripts/story_checks.py (no network)."""
import sys, unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import story_checks as s  # noqa: E402

LISTING = '<a href="climdiv-tmnst-v1.0.0-20261005">x</a> <a href="climdiv-tmnst-v1.0.0-20261105">y</a> climdiv-tmxst-v1.0.0-20261105'


def line(state, year, vals):
    return f"{state:03d}028{year}" + "".join(f"{v:7.2f}" for v in vals)


class TestParse(unittest.TestCase):
    def test_latest_file(self):
        self.assertEqual(s.latest_file(LISTING, "tmn"), "climdiv-tmnst-v1.0.0-20261105")
        with self.assertRaises(ValueError):
            s.latest_file(LISTING, "pcp")

    def test_parse_keeps_states_and_conus_and_missing(self):
        text = "\n".join([line(1, 1895, [30.0] * 12), line(110, 1895, [31.0] * 12),
                          line(50, 1895, [0.0] * 12),          # Alaska: dropped
                          line(1, 2026, [40.0] * 10 + [-99.9, -99.9])])
        d = s.parse_climdiv(text)
        self.assertEqual(sorted(d), [1, 110])
        self.assertEqual(d[1][2026][10], None)
        self.assertEqual(d[1][1895][0], 30.0)


class TestShares(unittest.TestCase):
    def series(self, n=40, cold_year=1900, tie=None):
        out = {y: [50.0] * 12 for y in range(1895, 1895 + n)}
        out[cold_year] = [10.0] * 12
        if tie:
            out[tie] = [10.0] * 12
        return out

    def test_record_goes_to_coldest_year_and_chance_sums_to_12(self):
        t = s.record_shares(self.series(), cold=True, last_year=2100)
        self.assertAlmostEqual(t[1900][0], 12)
        self.assertAlmostEqual(sum(v[1] for v in t.values()), 12)
        self.assertAlmostEqual(sum(v[0] for v in t.values()), 12)

    def test_ties_share(self):
        t = s.record_shares(self.series(tie=1910), cold=True, last_year=2100)
        self.assertAlmostEqual(t[1900][0], 6)
        self.assertAlmostEqual(t[1910][0], 6)

    def test_short_series_skipped(self):
        self.assertEqual(dict(s.record_shares(self.series(n=20), cold=True, last_year=2100)), {})

    def test_summary_since_2000(self):
        ser = {y: [50.0] * 12 for y in range(1980, 2020)}   # 1980-2019, 40 years, record in 2005
        ser[2005] = [10.0] * 12
        out = s.summarize({1: s.record_shares(ser, cold=True, last_year=2100)})
        self.assertEqual(out["records_since"], 12)
        self.assertEqual(out["chance_since"], 6.0)           # 20 of 40 years since 2000
        self.assertEqual(out["ratio_since"], 2.0)


class TestStations(unittest.TestCase):
    def test_ghcn_id(self):
        self.assertEqual(s.ghcn_id(["13889 1", "USW00013889 6", "JAX 3"]), "USW00013889")
        self.assertIsNone(s.ghcn_id(["13889 1"]))

    def test_km(self):
        self.assertAlmostEqual(s.km(41.7, -86.3, 41.6, -85.9), 35, delta=2)   # South Bend to Goshen

    def test_cities_found(self):
        c = s.load_cities()
        for slug in s.EXCEPTIONS + s.CONTROLS:
            self.assertIn(slug, c)


if __name__ == "__main__":
    unittest.main()
