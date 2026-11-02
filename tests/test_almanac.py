"""Offline tests of almanac/build.py (no network). Uses the real history/ files.

    python -m unittest discover tests
"""
import json, shutil, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "almanac"))
import build as a  # noqa: E402

REAL_HISTORY = REPO / "history"


class Test(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.hist = self.tmp / "history"
        self.hist.mkdir()
        self.out = self.tmp / "site"
        a.HISTORY_DIR = self.hist

    def tearDown(self):
        a.HISTORY_DIR = REAL_HISTORY
        shutil.rmtree(self.tmp)

    def run_all(self):
        sys.argv = ["build.py", "--all", "--out", str(self.out)]
        return a.main()

    def test_every_real_city_builds_with_all_claims_true(self):
        a.HISTORY_DIR = REAL_HISTORY
        self.assertEqual(self.run_all(), 0)
        slugs = [s for s in a.CITIES if (REAL_HISTORY / f"{s}.csv").exists()]
        for s in slugs:
            page = (self.out / "almanac" / s / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("{{", page)
            self.assertNotIn("../../../", page)          # links to the main site are absolute
            self.assertIn('href="../"', page)            # back to the Almanac index
        index = (self.out / "almanac" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("{{", index)
        for s in slugs:
            self.assertIn(f'"slug":"{s}"', index)            # one map dot / table row per place
        self.assertTrue((self.out / "index.html").exists())

    def test_no_placeholder_headlines_and_dots_are_on_the_map(self):
        a.HISTORY_DIR = REAL_HISTORY
        for s in [s for s in a.CITIES if (REAL_HISTORY / f"{s}.csv").exists()]:
            d = a.build(s)
            self.assertFalse(d["text"]["t_chg"].startswith("How "), s)
            row = a.index_row(d)
            self.assertTrue(0 < row["x"] < 975 and 0 < row["y"] < 610, s)

    def test_projection_matches_the_standard_us_map(self):
        # d3.geoAlbersUsa().scale(1300).translate([487.5, 305]) puts Chicago near (638, 227)
        x, y = a.project(-87.65, 41.85)
        self.assertAlmostEqual(x, 637.9, delta=1); self.assertAlmostEqual(y, 226.8, delta=1)

    def test_switch_links_goshen_and_south_bend_both_ways(self):
        # Stand in a copy of Goshen as South Bend, so the test needs only one file.
        shutil.copy(REAL_HISTORY / "goshen.csv", self.hist / "goshen.csv")
        shutil.copy(REAL_HISTORY / "goshen.csv", self.hist / "south-bend.csv")
        self.assertEqual(self.run_all(), 0)
        g = (self.out / "almanac" / "goshen" / "index.html").read_text(encoding="utf-8")
        sb = (self.out / "almanac" / "south-bend" / "index.html").read_text(encoding="utf-8")
        for page, here in ((g, "goshen"), (sb, "south-bend")):
            self.assertIn('class="switch"', page)
            self.assertIn(f'href="../{here}/" aria-current="page"', page)
        self.assertIn('href="../south-bend/"', g)
        self.assertIn('href="../goshen/"', sb)

    def test_no_switch_until_both_records_exist(self):
        shutil.copy(REAL_HISTORY / "goshen.csv", self.hist / "goshen.csv")
        self.assertEqual(self.run_all(), 0)
        g = (self.out / "almanac" / "goshen" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('class="switch"', g)

    def test_a_city_that_fails_a_check_is_skipped_and_the_rest_still_build(self):
        shutil.copy(REAL_HISTORY / "chicago.csv", self.hist / "chicago.csv")
        # Phoenix cut to its last 20 years: too short for trends (needs 30), so its page is skipped.
        lines = (REAL_HISTORY / "phoenix.csv").read_text(encoding="utf-8").splitlines()
        short = [lines[0]] + [l for l in lines[1:] if l[:4] >= "2006"]
        (self.hist / "phoenix.csv").write_text("\n".join(short) + "\n", encoding="utf-8")
        self.assertEqual(self.run_all(), 0)
        self.assertTrue((self.out / "almanac" / "chicago" / "index.html").exists())
        self.assertFalse((self.out / "almanac" / "phoenix" / "index.html").exists())
        index = (self.out / "almanac" / "index.html").read_text(encoding="utf-8")
        self.assertIn('"slug":"chicago"', index)
        self.assertNotIn('"slug":"phoenix"', index)

    def test_every_place_on_the_list_lands_inside_its_state(self):
        # catches a typo in a latitude/longitude: each dot must fall inside (or within ~20 km of)
        # its own state's outline on the map. Cincinnati's and Washington's records sit just across a border.
        states = json.loads((REPO / "almanac" / "us_states.json").read_text())["states"]
        def polys(d):
            out = []
            for part in d.split("Z"):
                pts = [tuple(map(float, xy.split(","))) for xy in part.lstrip("M").split("L") if xy]
                if len(pts) > 2: out.append(pts)
            return out
        def inside(x, y, poly):
            n, c = len(poly), False
            for i in range(n):
                (x1, y1), (x2, y2) = poly[i], poly[i - 1]
                if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
            return c
        def near(x, y, d, tol=4):
            return any(inside(x + dx, y + dy, p) for p in polys(d) for dx in (-tol, 0, tol) for dy in (-tol, 0, tol))
        for slug, c in a.CITIES.items():
            x, y = a.project(c["lon"], c["lat"])
            self.assertTrue(near(x, y, states[c["state"]]), f"{slug} is not inside {c['state']}")

    def test_a_record_that_looks_wrong_goes_to_review_not_the_site(self):
        lines = (REAL_HISTORY / "chicago.csv").read_text(encoding="utf-8").splitlines()
        head = lines[0].split(","); i = head.index("MaxTempF")
        row = lines[-1].split(","); row[i] = "151.0"                 # an impossible high
        (self.hist / "chicago.csv").write_text("\n".join(lines[:-1] + [",".join(row)]) + "\n", encoding="utf-8")
        shutil.copy(REAL_HISTORY / "miami.csv", self.hist / "miami.csv")
        self.assertEqual(self.run_all(), 0)
        self.assertFalse((self.out / "almanac" / "chicago" / "index.html").exists())
        self.assertTrue((self.out / "almanac" / "miami" / "index.html").exists())
        with self.assertRaises(a.QualityError):
            a.build("chicago")

    def test_rank_is_years_strictly_warmer_plus_one_with_ties_shared(self):
        r = a.same_date_rank({2000: 80, 2001: 85, 2002: 80, 2003: 70, 2026: 80}, 2026, 80)
        self.assertEqual(r["warm_rank"], 2)              # only 2001 was warmer
        self.assertEqual(r["tied"], [2000, 2002])
        self.assertEqual(r["years"], 5)


if __name__ == "__main__":
    unittest.main()
