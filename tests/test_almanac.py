"""Offline tests of almanac/build.py (no network). Uses the real history/ files.

    python -m unittest discover tests
"""
import contextlib, io, json, re, shutil, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "almanac"))
import build as a  # noqa: E402
import history_store as store  # noqa: E402  (build.py put scripts/ on the path)

REAL_HISTORY = REPO / "history"


def real(slug):
    """A place's real record (works with the old single files and the new decade files)."""
    return store.read_rows(REAL_HISTORY, slug)


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
        # Every place either builds, or is held back on purpose: SKIP (a claim isn't true
        # for it) or REVIEW (its record looks wrong). A crash (FAIL) is a bug. Held-back
        # places get no page, so the page checks below cover the ones that were built.
        a.HISTORY_DIR = REAL_HISTORY
        log = io.StringIO()
        with contextlib.redirect_stdout(log):
            self.assertEqual(self.run_all(), 0)
        crashes = [l for l in log.getvalue().splitlines() if re.match(r"^.{14} FAIL ", l)]
        self.assertEqual(crashes, [])
        listed = [s for s in a.CITIES if store.exists(REAL_HISTORY, s)]
        slugs = [s for s in listed if (self.out / "almanac" / s / "index.html").exists()]
        self.assertGreaterEqual(len(slugs), 0.95 * len(listed))   # a mass skip is a bug too
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
        for s in [s for s in a.CITIES if store.exists(REAL_HISTORY, s)]:
            try:
                d = a.build(s)
            except (a.ClaimError, a.QualityError):
                continue   # held back on purpose; no page, no dot
            self.assertFalse(d["text"]["t_chg"].startswith("How "), s)
            row = a.index_row(d)
            self.assertTrue(0 < row["x"] < 975 and 0 < row["y"] < 610, s)

    def test_projection_matches_the_standard_us_map(self):
        # d3.geoAlbersUsa().scale(1300).translate([487.5, 305]) puts Chicago near (638, 227)
        x, y = a.project(-87.65, 41.85)
        self.assertAlmostEqual(x, 637.9, delta=1); self.assertAlmostEqual(y, 226.8, delta=1)

    def test_switch_links_goshen_and_south_bend_both_ways(self):
        # Stand in a copy of Goshen as South Bend, so the test needs only one file.
        store.write_rows(self.hist, "goshen", real("goshen"))
        store.write_rows(self.hist, "south-bend", real("goshen"))
        self.assertEqual(self.run_all(), 0)
        g = (self.out / "almanac" / "goshen" / "index.html").read_text(encoding="utf-8")
        sb = (self.out / "almanac" / "south-bend" / "index.html").read_text(encoding="utf-8")
        for page, here in ((g, "goshen"), (sb, "south-bend")):
            self.assertIn('class="switch"', page)
            self.assertIn(f'href="../{here}/" aria-current="page"', page)
        self.assertIn('href="../south-bend/"', g)
        self.assertIn('href="../goshen/"', sb)

    def test_no_switch_until_both_records_exist(self):
        store.write_rows(self.hist, "goshen", real("goshen"))
        self.assertEqual(self.run_all(), 0)
        g = (self.out / "almanac" / "goshen" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('class="switch"', g)

    def test_a_city_that_fails_a_check_is_skipped_and_the_rest_still_build(self):
        store.write_rows(self.hist, "chicago", real("chicago"))
        # Phoenix cut to its last 20 years: too short for trends (needs 30), so its page is skipped.
        store.write_rows(self.hist, "phoenix", [r for r in real("phoenix") if r["Date"] >= "2006"])
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
        # Small islands the simplified outlines leave out; their locations were checked by hand (Nov 3, 2026).
        islands = {"st-paul-island", "key-west", "marathon"}
        for slug, c in a.CITIES.items():
            x, y = a.project(c["lon"], c["lat"])
            if slug in islands:
                self.assertTrue(0 < x < 975 and 0 < y < 610, f"{slug} is off the map")
                continue
            self.assertTrue(near(x, y, states[c["state"]]), f"{slug} is not inside {c['state']}")

    def test_a_record_that_looks_wrong_goes_to_review_not_the_site(self):
        rows = real("chicago"); rows[-1] = {**rows[-1], "MaxTempF": "151.0"}   # an impossible high
        store.write_rows(self.hist, "chicago", rows)
        store.write_rows(self.hist, "miami", real("miami"))
        self.assertEqual(self.run_all(), 0)
        self.assertFalse((self.out / "almanac" / "chicago" / "index.html").exists())
        self.assertTrue((self.out / "almanac" / "miami" / "index.html").exists())
        with self.assertRaises(a.QualityError):
            a.build("chicago")

    def test_old_single_files_and_decade_files_build_the_same_page(self):
        rows = real("seattle")
        old = self.tmp / "old"; old.mkdir()
        with (old / "seattle.csv").open("w", newline="", encoding="utf-8") as f:
            import csv
            w = csv.DictWriter(f, fieldnames=store.COLUMNS); w.writeheader(); w.writerows(rows)
        store.write_rows(self.hist, "seattle", rows)
        a.HISTORY_DIR = old; d_old = a.build("seattle")
        a.HISTORY_DIR = self.hist; d_new = a.build("seattle")
        self.assertEqual(json.dumps(d_old, sort_keys=True), json.dumps(d_new, sort_keys=True))

    def test_rank_is_years_strictly_warmer_plus_one_with_ties_shared(self):
        r = a.same_date_rank({2000: 80, 2001: 85, 2002: 80, 2003: 70, 2026: 80}, 2026, 80)
        self.assertEqual(r["warm_rank"], 2)              # only 2001 was warmer
        self.assertEqual(r["tied"], [2000, 2002])
        self.assertEqual(r["years"], 5)


if __name__ == "__main__":
    unittest.main()
