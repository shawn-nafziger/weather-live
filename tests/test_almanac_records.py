"""Offline tests of the Almanac's records pages (almanac/records.py, Nov 12, 2026). No network; uses the real
history/ files.

    python -m unittest tests.test_almanac_records
"""
import html, json, re, shutil, sys, tempfile, unittest
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "almanac"))
import build as a  # noqa: E402
import records as r  # noqa: E402
import history_store as store  # noqa: E402

REAL_HISTORY = REPO / "history"


def real(slug):
    return store.read_rows(REAL_HISTORY, slug)


def visible_text(page):
    """What a crawler reads without running scripts: the HTML with scripts and styles removed."""
    t = re.sub(r"<(script|style)\b.*?</\1>", " ", page, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", t))


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

    def build_one(self, slug, today=date(2026, 11, 10)):
        h = a.load(slug, None)
        d = a.build(slug, h=h)
        return d, h, r.build_records(d, h, a.CITIES, today=today)

    def test_records_recounted_from_the_raw_rows(self):
        # Every daily record on the page, recounted straight from the decade files without pandas.
        a.HISTORY_DIR = REAL_HISTORY
        d, h, rd = self.build_one("chicago")
        hi, lo = {}, {}
        for row in real("chicago"):
            md = row["Date"][5:10]
            if row["MaxTempF"] not in ("", None):
                v = a.whole_f(float(row["MaxTempF"]))
                hi.setdefault(md, []).append((v, int(row["Date"][:4])))
            if row["MinTempF"] not in ("", None):
                v = a.whole_f(float(row["MinTempF"]))
                lo.setdefault(md, []).append((v, int(row["Date"][:4])))
        for day in rd["daily"]:
            if not day["shown"]:
                continue
            md = day["md"]
            top = max(v for v, _ in hi[md]); bot = min(v for v, _ in lo[md])
            self.assertEqual(day["hi"], top, md)
            self.assertEqual(day["lo"], bot, md)
            self.assertEqual(day["hi_y"], sorted(y for v, y in hi[md] if v == top), md)
            self.assertEqual(day["lo_y"], sorted(y for v, y in lo[md] if v == bot), md)
        # Chicago's all-time records, as NOAA's Chicago office lists them
        self.assertEqual((rd["alltime"]["lo"]["v"], rd["alltime"]["lo"]["days"]), (-27, ["1985-01-20"]))
        self.assertEqual((rd["alltime"]["hi"]["v"], rd["alltime"]["hi"]["days"]), (105, ["1934-07-24"]))

    def test_sentences_match_the_numbers(self):
        a.HISTORY_DIR = REAL_HISTORY
        d, h, rd = self.build_one("chicago")
        t = rd["text"]
        self.assertIn("−27°F, on January 20, 1985", t["dek"])
        self.assertIn("105°F, on July 24, 1934", t["dek"])
        self.assertEqual(t["t_year"], "Chicago's records are farthest apart in January")
        self.assertIn("On January 20, Chicago's record high and record low are <strong>90°F apart</strong>", t["p_year"])
        self.assertIn("on July 12, just <strong>44°F</strong>", t["p_year"])
        # today = Nov 10: 76°F in 2022, 18°F in 1986 and 2017, out of 154 November 10ths (1872-2025)
        self.assertIn("a high of <strong>76°F</strong> (2022) and a low of <strong>18°F</strong> (1986 and 2017), "
                      "from 154 November 10ths, 1872 to 2025", t["p_today"])
        self.assertIn("November 12, 2019", t["p_day"])
        self.assertEqual(rd["title"], "Chicago Record Low and High Temperatures")
        self.assertLessEqual(len(rd["title"] + r.SUFFIX), 62)
        self.assertTrue(120 <= len(rd["description"]) <= 160, rd["description"])

    def test_second_stations_get_no_branch_pages(self):
        for slug in ("new-york-jfk", "new-york-laguardia", "dallas-love-field", "austin-bergstrom", "washington-dulles"):
            if slug in a.CITIES:
                self.assertFalse(r.has_branch_pages(slug, a.CITIES), slug)
        for slug in ("new-york", "dallas", "chicago", "utqiagvik-barrow"):
            if slug in a.CITIES:
                self.assertTrue(r.has_branch_pages(slug, a.CITIES), slug)

    def test_pages_are_built_linked_and_readable_without_scripts(self):
        for s in ("chicago", "milwaukee", "rockford", "phoenix", "new-york", "new-york-jfk"):
            store.write_rows(self.hist, s, real(s))
        self.assertEqual(self.run_all(), 0)
        alm = self.out / "almanac"
        for s in ("chicago", "milwaukee", "rockford", "phoenix", "new-york"):
            page = (alm / s / "records" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("{{", page)
            self.assertEqual(page.count("<h1"), 1)
            self.assertNotIn("noindex", page)
            self.assertIn(f'<link rel="canonical" href="{a.ALMANAC}{s}/records/">', page)
            ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
            types = {n["@type"] for n in ld["@graph"]}
            self.assertTrue({"WebPage", "Dataset", "Place", "BreadcrumbList"} <= types, s)
            title = re.search(r"<title>(.*?)</title>", page).group(1)
            self.assertLessEqual(len(title), 62, title)
            desc = html.unescape(re.search(r'<meta name="description" content="([^"]*)"', page).group(1))
            self.assertTrue(120 <= len(desc) <= 160, desc)
            self.assertIn('href="../../"', page)          # Almanac front page
            self.assertIn('href="../"', page)             # the city's overview
            text = visible_text(page)
            # headings, findings and every month's table are in the HTML itself
            self.assertIn("record low is", text)
            self.assertIn("farthest apart in", text)
            for m in r.MONTHS:
                self.assertIn(f"{m} daily records in", text)
            self.assertGreaterEqual(len(re.findall(r'<tr[^>]* id="d-\d\d-\d\d"', page)), 365)
            # the overview links to its records page, and the sitemap lists it
            ov = (alm / s / "index.html").read_text(encoding="utf-8")
            self.assertIn('href="records/"', ov)
            self.assertIn("Weather History · Tall Order Digital</title>", ov)
            self.assertIn(f"<loc>{a.ALMANAC}{s}/records/</loc>", (alm / "sitemap.xml").read_text())
        # nearby links point only at records pages that were built
        chi = (alm / "chicago" / "records" / "index.html").read_text(encoding="utf-8")
        for target in re.findall(r'href="\.\./\.\./([^/]+)/records/"', chi):
            self.assertTrue((alm / target / "records" / "index.html").exists(), target)
        # a second station keeps its overview, with no tabs and no records page
        self.assertTrue((alm / "new-york-jfk" / "index.html").exists())
        self.assertFalse((alm / "new-york-jfk" / "records").exists())
        self.assertNotIn('class="tabs"', (alm / "new-york-jfk" / "index.html").read_text(encoding="utf-8"))

    def test_records_pages_differ_between_neighbors(self):
        # A records page should be mostly its own place's numbers (seo-standards §6): two neighbors share
        # well under half of their readable text, in 5-word runs.
        for s in ("chicago", "rockford"):
            store.write_rows(self.hist, s, real(s))
        self.assertEqual(self.run_all(), 0)
        def runs(s):
            w = visible_text((self.out / "almanac" / s / "records" / "index.html").read_text(encoding="utf-8")).split()
            return {tuple(w[i:i + 5]) for i in range(len(w) - 4)}
        A, B = runs("chicago"), runs("rockford")
        self.assertLess(len(A & B) / len(A), 0.4)

    def test_a_records_page_that_fails_today_keeps_its_last_good_version(self):
        import carry_over as co
        prev, new = self.tmp / "prev", self.tmp / "new"
        for d, rec, day in ((prev, range(20), "2026-11-09"), (new, range(19), "2026-11-10")):
            for i in range(20):
                (d / f"p{i}").mkdir(parents=True)
                (d / f"p{i}" / "index.html").write_text(f"p{i} {day}")
            for i in rec:
                (d / f"p{i}" / "records").mkdir()
                (d / f"p{i}" / "records" / "index.html").write_text(f"p{i} records {day}")
            (d / "index.html").write_text("front")
            (d / "sitemap.xml").write_text("<urlset>\n" + "\n".join(
                [f"  <url><loc>https://x/weather/almanac/p{i}/</loc><lastmod>{day}</lastmod></url>" for i in range(20)]
                + [f"  <url><loc>https://x/weather/almanac/p{i}/records/</loc><lastmod>{day}</lastmod></url>" for i in rec])
                + "\n</urlset>\n")
        cities = {f"p{i}": {} for i in range(20)}
        self.assertEqual(co.carry_over(new, prev, cities), ["p19/records"])
        self.assertEqual((new / "p19" / "records" / "index.html").read_text(), "p19 records 2026-11-09")
        self.assertEqual((new / "p19" / "index.html").read_text(), "p19 2026-11-10")   # today's overview untouched
        self.assertIn("almanac/p19/records/</loc><lastmod>2026-11-09", (new / "sitemap.xml").read_text())
        # far fewer records pages than last time: stop instead of publishing
        for i in range(5):
            shutil.rmtree(new / f"p{i}" / "records")
        with self.assertRaises(SystemExit):
            co.carry_over(new, prev, cities)


if __name__ == "__main__":
    unittest.main()
