"""
station_update.py

DAILY (run by GitHub Actions, see .github/workflows/daily.yml).

Keeps history/<city>.csv: each city's official daily record (high, low,
precipitation, snowfall) from NOAA via RCC-ACIS, using the ThreadEx "official
city record" where one exists. Cities come from config/cities.json (the ones
the hourly job also follows) and config/almanac_cities.json (Almanac only).

- First run for a city: downloads its whole record. At most
  MAX_FULL_DOWNLOADS_PER_RUN cities are downloaded in full per run, so a batch of
  new cities arrives over a few runs instead of one long one.
- After that: re-downloads the last REFRESH_DAYS days (stations post late and
  correct values) and replaces just those days. Older history is untouched,
  and no day is ever dropped.

Writes history/status.json (what happened to each city this run, including
cities still waiting for their first download), and ends by printing each city's latest official high and how it ranks among every
same calendar date, to compare with the NWS daily climate report.

This is the same job as weather-intelligence/pipeline/12, moved here so it
runs every morning without Shawn's PC.

    python scripts/station_update.py
"""

import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

import ranks

REPO = Path(__file__).resolve().parents[1]
CITIES_FILE = REPO / "config" / "cities.json"
ALMANAC_CITIES_FILE = REPO / "config" / "almanac_cities.json"
HISTORY_DIR = REPO / "history"
STATUS_FILE_NAME = "status.json"

ACIS_DATA_URL = "https://data.rcc-acis.org/StnData"
USER_AGENT = "(tallorderdigital.com weather-live, https://github.com/shawn-nafziger/weather-live)"
TIMEOUT_SECONDS = 120          # a full record is one large response
REFRESH_DAYS = 21
RETRIES = 3
PAUSE_SECONDS = 1
MAX_FULL_DOWNLOADS_PER_RUN = 15  # first-time downloads are large; spread a new batch over runs

COLUMNS = ["Date", "MaxTempF", "MinTempF", "PrecipIn", "SnowIn", "PrecipTrace", "SnowTrace",
           "MaxTempC", "MinTempC", "PrecipMM", "SnowCM"]


def acis_data(session, sid, start, end):
    params = {"sid": sid, "sdate": start, "edate": end,
              "elems": [{"name": "maxt"}, {"name": "mint"}, {"name": "pcpn"}, {"name": "snow"}]}
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.post(ACIS_DATA_URL, json=params, timeout=TIMEOUT_SECONDS)
            r.raise_for_status()
            payload = r.json()
            if "error" in payload:
                raise RuntimeError(f"ACIS: {payload['error']}")
            return payload.get("data", [])
        except Exception as e:  # ACIS hiccups; try again
            last = e
            time.sleep(5 * attempt)
    raise RuntimeError(f"{sid}: {last}")


def parse(raw):
    """ACIS value -> (number or None, is_trace). 'M' missing, 'T' trace, '12A' accumulated."""
    text = "" if raw is None else str(raw).strip()
    if text in ("", "M", "S"):
        return None, False
    if text == "T":
        return 0.0, True
    try:
        return float(text.rstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ")), False
    except ValueError:
        return None, False


def to_row(day, maxt, mint, pcpn, snow):
    tmax, _ = parse(maxt)
    tmin, _ = parse(mint)
    prcp, prcp_t = parse(pcpn)
    snwf, snow_t = parse(snow)
    if tmax is None and tmin is None and prcp is None and snwf is None:
        return None
    f2c = lambda f: "" if f is None else round((f - 32) * 5 / 9, 2)  # noqa: E731
    blank = lambda v: "" if v is None else v  # noqa: E731
    return {
        "Date": day,
        "MaxTempF": blank(tmax), "MinTempF": blank(tmin), "PrecipIn": blank(prcp), "SnowIn": blank(snwf),
        "PrecipTrace": prcp_t, "SnowTrace": snow_t,
        "MaxTempC": f2c(tmax), "MinTempC": f2c(tmin),
        "PrecipMM": "" if prcp is None else round(prcp * 25.4, 2),
        "SnowCM": "" if snwf is None else round(snwf * 2.54, 2),
    }


def read_history(path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_history(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    tmp.replace(path)


def update_city(session, city, today):
    path = HISTORY_DIR / f"{city['slug']}.csv"
    old = read_history(path)
    if not old:
        data = acis_data(session, city["record_sid"], "por", "por")
        start = None
    else:
        start = (today - timedelta(days=REFRESH_DAYS)).isoformat()
        data = acis_data(session, city["record_sid"], start, today.isoformat())
    fresh = [r for r in (to_row(*row[:5]) for row in data) if r]

    # New values replace old ones day by day. A day ACIS no longer sends (for
    # example, now all missing) keeps its old values rather than disappearing.
    merged = {r["Date"]: r for r in old}
    merged.update({r["Date"]: r for r in fresh})
    rows = sorted(merged.values(), key=lambda r: r["Date"])

    # Safety checks before writing anything.
    dates = [r["Date"] for r in rows]
    if len(rows) < len(old):
        raise RuntimeError(f"row count would fall from {len(old):,} to {len(rows):,}; file not written")
    if old and not fresh:
        raise RuntimeError("ACIS returned no recent days; file not written")

    write_history(path, rows)
    return {"rows_before": len(old), "rows_after": len(rows),
            "first": dates[0] if dates else None, "last": dates[-1] if dates else None,
            "full_download": not old}


def latest_high_line(city):
    history = ranks.load_history(HISTORY_DIR / f"{city['slug']}.csv")
    days = sorted(d for d, r in history.items() if r["MaxTempF"] not in ("", None))
    if not days:
        return "no highs yet"
    day = days[-1]
    r = ranks.rank(history, day, history[day]["MaxTempF"])
    return f"{day}: high {ranks.whole_f(history[day]['MaxTempF'])}°F, {ranks.phrase(r, day)}"


def load_cities():
    """Every city the daily job keeps: cities.json first, then Almanac-only places (no duplicates)."""
    cities = json.loads(CITIES_FILE.read_text(encoding="utf-8"))["cities"]
    if ALMANAC_CITIES_FILE.exists():
        seen = {c["slug"] for c in cities}
        cities += [c for c in json.loads(ALMANAC_CITIES_FILE.read_text(encoding="utf-8"))["cities"]
                   if c["slug"] not in seen]
    return cities


def main():
    cities = load_cities()
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    today = date.today()
    failures = full = 0
    status = {"run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "cities": {}}
    for city in cities:
        new = not (HISTORY_DIR / f"{city['slug']}.csv").exists()
        if new and full >= MAX_FULL_DOWNLOADS_PER_RUN:
            status["cities"][city["slug"]] = {"ok": False, "waiting": True,
                                              "note": "first download waits for a later run"}
            continue
        full += new
        try:
            res = update_city(session, city, today)
            how = "full record downloaded" if res["full_download"] else f"last {REFRESH_DAYS} days refreshed"
            print(f"{city['name']:<14} OK   {how}: {res['rows_before']:,} -> {res['rows_after']:,} days"
                  f" ({res['first']} to {res['last']})")
            print(f"{'':<19}{latest_high_line(city)}")
            status["cities"][city["slug"]] = {"ok": True, **res}
        except Exception as e:
            failures += 1
            print(f"{city['name']:<14} FAIL {e}")
            status["cities"][city["slug"]] = {"ok": False, "error": str(e)[:300], "sid": city["record_sid"]}
        time.sleep(PAUSE_SECONDS)
    waiting = sum(1 for v in status["cities"].values() if v.get("waiting"))
    attempted = len(cities) - waiting
    status["summary"] = {"cities": len(cities), "updated": attempted - failures, "failed": failures, "waiting": waiting}
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    (HISTORY_DIR / STATUS_FILE_NAME).write_text(json.dumps(status, indent=1), encoding="utf-8")
    print(f"{attempted - failures} of {attempted} cities updated"
          + (f"; {waiting} waiting for their first download." if waiting else "."))
    return 1 if attempted and failures == attempted else 0


if __name__ == "__main__":
    sys.exit(main())
