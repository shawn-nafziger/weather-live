"""
forecast_pull.py: fetch every Almanac place's 7-day NWS forecast into a work folder, just before the pages are
built (daily workflow). Nothing here is committed. See almanac/forecast.py.

    python scripts/forecast_pull.py --out forecasts_tmp
"""
import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "almanac"))
import build  # noqa: E402  (CITIES: every Almanac place, with its record's location)
import forecast  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    places = {s: (c["lat"], c["lon"]) for s, c in build.CITIES.items()
              if build.store.exists(build.HISTORY_DIR, s)}
    ok, failed = forecast.fetch_all(places, a.out)
    print(f"Forecasts: {ok} of {len(places)} places.")
    for s, e in sorted(failed.items()):
        print(f"  no forecast for {s}: {e}")
    # Never fail the job: a place without a forecast just has no strip today.
    return 0


if __name__ == "__main__":
    sys.exit(main())
