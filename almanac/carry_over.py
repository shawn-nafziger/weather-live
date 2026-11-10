"""
Keep the Almanac steady between builds (Nov 10, 2026). Run by the daily workflow after build.py, before the
pages are handed to the main site:

    python almanac/carry_over.py site/almanac prev

site/almanac is today's build; prev is the last published build (the almanac-site branch), if there is one.

- A place whose checks fail today (build.py skips it rather than publish a wrong sentence) keeps its last good
  page, and its sitemap entry keeps that page's date. Search engines never see a page flicker to "not found",
  and the "Nearby" links on other pages keep working. Its page says which day its numbers are through.
- If today's build has far fewer pages than the last one (under 90%), something is wrong with the run, not the
  weather: this stops with an error, nothing is handed over, and the site keeps yesterday's Almanac.
- A place no longer in config/almanac_cities.json is not carried over (its page is retired on purpose).
"""
import re
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build import CITIES  # noqa: E402

MIN_SHARE = 0.9


def places(d):
    return {p.parent.name for p in d.glob("*/index.html")} if d.is_dir() else set()


def carry_over(new, prev, cities=None):
    """Copy places missing from today's build back from the previous one. Returns the slugs carried over."""
    cities = CITIES if cities is None else cities
    built, before = places(new), places(prev)
    if before and len(built) < MIN_SHARE * len(before):
        raise SystemExit(f"Only {len(built)} Almanac pages built, against {len(before)} last time. "
                         "Not publishing; the site keeps the last good Almanac.")
    kept = sorted(s for s in before - built if s in cities)
    if not kept:
        return kept
    for slug in kept:
        shutil.copytree(prev / slug, new / slug)
    old_map, new_map = prev / "sitemap.xml", new / "sitemap.xml"
    old = old_map.read_text(encoding="utf-8") if old_map.exists() else ""
    entries = [m.group(0) for s in kept
               for m in [re.search(rf"  <url><loc>[^<]*/{re.escape(s)}/</loc>.*?</url>", old)] if m]
    if entries and new_map.exists():
        text = new_map.read_text(encoding="utf-8").replace("</urlset>", "\n".join(entries) + "\n</urlset>", 1)
        new_map.write_text(text, encoding="utf-8")
    return kept


def main():
    new, prev = Path(sys.argv[1]), Path(sys.argv[2])
    if not (new / "index.html").exists():
        raise SystemExit(f"No Almanac build in {new}.")
    kept = carry_over(new, prev)
    for s in kept:
        print(f"{CITIES[s]['name']:<14} KEPT  today's checks didn't pass; last good page kept")
    print(f"{len(places(new))} Almanac pages ready ({len(kept)} kept from the last build).")


if __name__ == "__main__":
    main()
