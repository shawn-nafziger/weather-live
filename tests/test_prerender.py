"""Offline tests of the text written into the HTML (almanac/prerender.py), the published dates and the shared
"How the Almanac works" page (Nov 24, 2026). Uses the real history/ files.

The full check that the HTML matches what the page script draws (all 19 elements, every place) was run in a
browser while building this; here the same is checked for a few blocks against the page script's own wording."""
import json, re, shutil, sys, tempfile, unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "almanac"))
import build as a  # noqa: E402
import prerender as pr  # noqa: E402


class TestFormatting(unittest.TestCase):
    def test_numbers_round_like_javascript(self):
        # toFixed rounds halves up (on the exact binary value); Math.round rounds halves toward +infinity
        self.assertEqual(pr.fixed(0.125, 2), "0.13")
        self.assertEqual(pr.fixed(2.5, 0), "3")
        self.assertEqual(pr.fixed(1.005, 2), "1.00")       # 1.005 is just below 1.005 in binary, as in JavaScript
        self.assertEqual(pr.jsround(-2.5), -2)
        self.assertEqual(pr.fmt2(-0.0391), "−0.04")
        self.assertEqual(pr.fmt1(0), "0.0")


class TestPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        sys.argv = ["build.py", "chicago", "--out", str(cls.tmp)]
        a.main()
        cls.page = (cls.tmp / "almanac" / "chicago" / "index.html").read_text(encoding="utf-8")
        cls.data = json.loads((cls.tmp / "almanac" / "chicago" / "data.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def body_of(self, el_id):
        m = re.search(rf'id="{el_id}"[^>]*>(.*?)</(?:div|p|ul)>\s', self.page, re.S)
        return m.group(1) if m else ""

    def test_every_table_box_has_rows_in_the_html(self):
        for el_id in ("tb-yest", "tb-year", "tb-rec", "tb-ann", "tb-mon", "tb-heat", "tb-pr", "tb-dry"):
            m = re.search(rf'id="{el_id}">(<table>.*?</table>)', self.page, re.S)
            self.assertIsNotNone(m, el_id)
            self.assertGreater(m.group(1).count("<tr>"), 5, el_id)

    def test_year_table_has_one_row_per_day_so_far(self):
        m = re.search(r'id="tb-year">(<table>.*?</table>)', self.page, re.S)
        self.assertEqual(m.group(1).count("<tr>") - 1, len(self.data["season"]["year"]))

    def test_headline_numbers_and_record_book_are_in_the_html(self):
        y = self.data["yesterday"]
        self.assertIn(f'<span class="lab">Yesterday\'s high</span><b class="', self.page)
        self.assertIn(f'{y["hi"]}°F</b>', self.page)
        self.assertIn("<h3>Hottest days on record</h3>", self.page)
        self.assertIn(f"{self.data['season']['pct_above']}% of days in {self.data['year']} so far", self.page)

    def test_short_method_list_and_link_to_the_shared_page(self):
        self.assertIn("<strong>The record:</strong>", self.page)
        self.assertNotIn("<strong>Complete years:</strong>", self.page)   # moved to how-it-works
        self.assertIn('href="../how-it-works/"', self.page)

    def test_published_date_shown_and_in_structured_data(self):
        d = a.published("overview", "chicago")
        self.assertIn(f'Published <time datetime="{d}">', self.page)
        self.assertIn(f'"datePublished":"{d}"', self.page)


class TestPublished(unittest.TestCase):
    def test_every_place_has_a_published_date(self):
        missing = [s for s in a.CITIES if s not in a.PUBLISHED["overview"]]
        self.assertEqual(missing, [], "add these places to config/almanac_published.json")
        for kind in ("records", "every-day", "how-it-works"):
            self.assertRegex(a.published(kind), r"^2026-\d\d-\d\d$")


class TestHowPage(unittest.TestCase):
    def test_shared_page_is_written_with_its_own_title(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            d = a.build("chicago")
            a.write_how([d], tmp)
            page = (tmp / "almanac" / "how-it-works" / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("{{", page)
            self.assertIn("<title>How the Weather Almanac Works · Tall Order Digital</title>", page)
            self.assertIn('rel="canonical" href="https://tallorderdigital.com/weather/almanac/how-it-works/"', page)
            self.assertEqual(page.count("<h1>"), 1)
            for anchor in ("records", "ranks", "complete-years", "trends", "three-ways", "records-vs-chance", "forecast"):
                self.assertIn(f'id="{anchor}"', page)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
