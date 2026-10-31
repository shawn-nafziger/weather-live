"""Offline tests of almanac/build.py (no network). Uses the real history/ files.

    python -m unittest discover tests
"""
import shutil, sys, tempfile, unittest
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
        self.assertEqual(index.count('class="card"'), len(slugs))
        self.assertTrue((self.out / "index.html").exists())

    def test_switch_links_goshen_and_south_bend_both_ways(self):
        # No South Bend record yet in this repo: stand one in with Goshen's file.
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
        self.assertEqual(index.count('class="card"'), 1)

    def test_rank_is_years_strictly_warmer_plus_one_with_ties_shared(self):
        r = a.same_date_rank({2000: 80, 2001: 85, 2002: 80, 2003: 70, 2026: 80}, 2026, 80)
        self.assertEqual(r["warm_rank"], 2)              # only 2001 was warmer
        self.assertEqual(r["tied"], [2000, 2002])
        self.assertEqual(r["years"], 5)


if __name__ == "__main__":
    unittest.main()
