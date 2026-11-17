"""Offline tests of the Almanac's "Every day" pages (almanac/everyday.py, Nov 16, 2026). No network; uses the real
history/ files.

    python -m unittest tests.test_almanac_everyday
"""
import html, json, re, shutil, sys, tempfile, unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "almanac"))
import build as a  # noqa: E402
import everyday as ev  # noqa: E402
import carry_over  # noqa: E402
import history_store as store  # noqa: E402

REAL_HISTORY = REPO / "history"


def visible_text(page):
    """What a crawler reads without running scripts: the HTML with scripts and styles removed."""
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t)))


class Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.h = a.load("chicago")
        cls.ov = a.build("chicago", h=cls.h)
        cls.ed = ev.build_everyday(cls.ov, cls.h, a.CITIES, today=date(2026, 11, 16))

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        a.HISTORY_DIR = REAL_HISTORY
        shutil.rmtree(self.tmp)

    def test_encoding_round_trips_every_high(self):
        # Every day's high, recounted from the decade files without pandas, matches what the page script will decode.
        p = self.ed["payload"]
        got = ev.decode(p["rows"], p["exc"], p["y0"], p["norm"])
        want = {}
        for row in store.read_rows(REAL_HISTORY, "chicago"):
            y = int(row["Date"][:4])
            if row["MaxTempF"] not in ("", None) and p["y0"] <= y <= self.ed["year"]:
                want[(y, ev.idx_of(row["Date"][5:10]))] = a.whole_f(float(row["MaxTempF"]))
        self.assertEqual(got, want)
        self.assertTrue(all(len(r) == 366 for r in p["rows"]))
        self.assertTrue(set("".join(p["rows"])) <= set(ev.ALPHA + ev.MISSING + ev.EXCEPTION))
        self.assertNotIn("<", ev.ALPHA)
        self.assertNotIn("\\", ev.ALPHA)
        self.assertNotIn('"', ev.ALPHA)

    def test_normals_match_the_overview(self):
        s = self.ov["season"]
        norm = self.ed["payload"]["norm"]
        for md, v in zip(s["days"], s["norm_hi"]):
            self.assertAlmostEqual(norm[ev.idx_of(md)], v, places=1)

    def test_starts_in_1900_or_the_first_full_year(self):
        self.assertEqual(self.ed["y0"], 1900)
        h = a.load("seattle")
        ed = ev.build_everyday(a.build("seattle", h=h), h, a.CITIES)
        self.assertGreater(ed["y0"], 1900)
        self.assertIn(f"Since {ed['y0']}", ed["text"]["lede"])

    def test_lede_numbers_recounted(self):
        p = self.ed["payload"]
        days = ev.decode(p["rows"], p["exc"], p["y0"], p["norm"])
        up = sum(1 for (y, i), v in days.items() if v > p["norm"][i])
        self.assertIn(f"{round(100 * up / len(days))}% of {len(days):,} days", self.ed["text"]["lede"])
        early = [(y, i) for (y, i) in days if 1900 <= y <= 1909]
        e_up = sum(1 for k in early if days[k] > p["norm"][k[1]])
        self.assertIn(f"{round(100 * e_up / len(early))}% in the 1900s", self.ed["text"]["lede"])

    def test_page_is_readable_without_scripts(self):
        a.HISTORY_DIR = REAL_HISTORY
        page = ev.render(self.ed, seo_head=a.everyday_seo(self.ed), nearby="", main_site=a.MAIN_SITE)
        text = visible_text(page)
        for k in ("lede", "p_dec", "p_new", "p_years", "p_streak", "p_year"):
            if self.ed["text"][k]:
                self.assertIn(self.ed["text"][k], text, k)
        for d in self.ed["decades"]:
            self.assertIn(d["label"], text)
        self.assertEqual(page.count("<h1>"), 1)
        self.assertIn('<link rel="canonical" href="https://tallorderdigital.com/weather/almanac/chicago/every-day/">', page)
        self.assertIn('"@type":"Dataset"', page)
        self.assertNotIn("noindex", page)
        self.assertIn('<a href="../records/">', page)
        title = re.search(r"<title>(.*?)</title>", page).group(1)
        self.assertLessEqual(len(html.unescape(title)), ev.TITLE_MAX)
        desc = html.unescape(re.search(r'<meta name="description" content="(.*?)">', page).group(1))
        self.assertTrue(110 <= len(desc) <= 160, desc)
        self.assertIn("© 2026 Tall Order Digital LLC", page)

    def test_full_build_writes_pages_tabs_and_sitemap(self):
        hist = self.tmp / "history"
        hist.mkdir()
        for slug in ("chicago", "south-bend"):
            shutil.copytree(REAL_HISTORY / slug, hist / slug)
        a.HISTORY_DIR = hist
        out = self.tmp / "site"
        sys.argv = ["build.py", "--all", "--out", str(out)]
        self.assertEqual(a.main(), 0)
        for slug, other in (("chicago", "south-bend"), ("south-bend", "chicago")):
            page = (out / "almanac" / slug / "every-day" / "index.html").read_text(encoding="utf-8")
            self.assertIn(f'href="../../{other}/every-day/"', page)
            days = json.loads((out / "almanac" / slug / "every-day" / "days.json").read_text(encoding="utf-8"))
            self.assertEqual(days["slug"], slug)
            for parent in (out / "almanac" / slug / "index.html", out / "almanac" / slug / "records" / "index.html"):
                self.assertIn("every-day/", parent.read_text(encoding="utf-8"))
        sitemap = (out / "almanac" / "sitemap.xml").read_text(encoding="utf-8")
        self.assertIn("/weather/almanac/chicago/every-day/</loc>", sitemap)

    def test_carry_over_keeps_a_missing_every_day_page(self):
        prev, new = self.tmp / "prev", self.tmp / "new"
        for root in (prev, new):
            for slug in ("chicago", "south-bend"):
                (root / slug / "records").mkdir(parents=True)
                (root / slug / "index.html").write_text("x")
                (root / slug / "records" / "index.html").write_text("x")
        for slug in ("chicago", "south-bend"):
            (prev / slug / "every-day").mkdir()
            (prev / slug / "every-day" / "index.html").write_text("old")
        (new / "chicago" / "every-day").mkdir()
        (new / "chicago" / "every-day" / "index.html").write_text("new")
        # One of two every-day pages missing is under the 90% guard: nothing is handed over.
        with self.assertRaises(SystemExit):
            carry_over.carry_over(new, prev)
        cities = {f"p{i}": {} for i in range(12)} | {"chicago": {}, "south-bend": {}}
        for i in range(12):
            for root in (prev, new):
                (root / f"p{i}" / "every-day").mkdir(parents=True)
                (root / f"p{i}" / "index.html").write_text("x")
                (root / f"p{i}" / "every-day" / "index.html").write_text("x")
        kept = carry_over.carry_over(new, prev, cities)
        self.assertIn("south-bend/every-day", kept)
        self.assertEqual((new / "south-bend" / "every-day" / "index.html").read_text(), "old")


if __name__ == "__main__":
    unittest.main()
