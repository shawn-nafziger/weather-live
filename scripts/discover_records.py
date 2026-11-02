"""
discover_records.py

ONE-TIME (run by hand from GitHub Actions: "Find official city records").

Finds every official long-term city record ("ThreadEx" record) that NOAA's
RCC-ACIS serves, so the Almanac can grow from 50 metro areas to all of them.

ACIS has no "list every ThreadEx record" call, and its area search doesn't
return them. But their ids are almost always a 3-letter airport / weather
office code plus "thr" (ORDthr = Chicago, PHXthr = Phoenix, SBNthr = South
Bend). So this script takes every US airport with a 3-letter code from the
public OurAirports list, asks ACIS for the last 10 days of "<code>thr 9", and
keeps the ones that answer with current data.

Writes discovery/official_records.json:
    [{"sid": "SBNthr 9", "acis_name": "South Bend Area", "code": "SBN",
      "airport": "South Bend International Airport", "city": "South Bend",
      "state": "IN", "lat": 41.7, "lon": -86.3, "latest": "2026-11-01"}, ...]

The airport's location is used for the map dot (the record's current site is
usually that airport). Takes about 10-15 minutes.

    python scripts/discover_records.py
"""

import csv
import io
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
OUT_FILE = REPO / "discovery" / "official_records.json"
AIRPORTS_URL = "https://davidmegginson.github.io/ourairports-data/airports.csv"
ACIS_DATA_URL = "https://data.rcc-acis.org/StnData"
USER_AGENT = "(tallorderdigital.com weather-live, https://github.com/shawn-nafziger/weather-live)"
PAUSE_SECONDS = 0.3
# Weather-office codes that aren't airport codes but are known ThreadEx ids.
EXTRA_CODES = ["NYC", "ATT", "SAC", "LAX", "SFO", "DCA", "CHI", "HOU", "OKC"]
STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
             "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split())


def candidate_airports(csv_text):
    """{code: airport row} for US airports (50 states + DC) with a 3-letter code, biggest first."""
    rank = {"large_airport": 0, "medium_airport": 1, "small_airport": 2}
    out = {}
    for r in csv.DictReader(io.StringIO(csv_text)):
        if r.get("iso_country") != "US" or r.get("type") not in rank:
            continue
        st = (r.get("iso_region") or "").replace("US-", "")
        if st not in STATES:
            continue
        code = (r.get("iata_code") or "").strip().upper()
        if len(code) != 3 or not code.isalpha():
            continue
        if r["type"] == "small_airport" and r.get("scheduled_service") != "yes":
            continue                       # thousands of tiny strips; official records aren't there
        if code not in out or rank[r["type"]] < rank[out[code]["type"]]:
            out[code] = {"type": r["type"], "airport": r.get("name", ""), "city": r.get("municipality", ""),
                         "state": st, "lat": round(float(r["latitude_deg"]), 4), "lon": round(float(r["longitude_deg"]), 4)}
    return out


def probe(session, code, today):
    """The ThreadEx record '<code>thr 9' if ACIS serves it with data in the last 10 days, else None."""
    sid = f"{code}thr 9"
    try:
        r = session.post(ACIS_DATA_URL, json={
            "sid": sid, "sdate": (today - timedelta(days=10)).isoformat(), "edate": today.isoformat(),
            "elems": [{"name": "maxt"}], "meta": "name,state"}, timeout=60)
        r.raise_for_status()
        payload = r.json()
    except Exception:
        return None
    if "error" in payload:
        return None
    days = [d for d, v in payload.get("data", []) if str(v).strip() not in ("", "M", "S")]
    if not days:
        return None
    meta = payload.get("meta") or {}
    return {"sid": sid, "acis_name": meta.get("name", ""), "acis_state": meta.get("state", ""), "latest": max(days)}


def main():
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    airports = candidate_airports(session.get(AIRPORTS_URL, timeout=120).text)
    codes = sorted(set(airports) | set(EXTRA_CODES))
    print(f"Checking {len(codes)} codes against ACIS...")
    today, found = date.today(), []
    for i, code in enumerate(codes, 1):
        hit = probe(session, code, today)
        if hit:
            ap = airports.get(code, {})
            found.append({**hit, "code": code, "airport": ap.get("airport", ""), "city": ap.get("city", ""),
                          "state": ap.get("state") or hit["acis_state"], "lat": ap.get("lat"), "lon": ap.get("lon")})
            print(f"  {code}: {hit['acis_name']} (latest {hit['latest']})")
        if i % 100 == 0:
            print(f"  ... {i} of {len(codes)} checked, {len(found)} found")
        time.sleep(PAUSE_SECONDS)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps({"run": today.isoformat(), "checked": len(codes), "found": found}, indent=1),
                        encoding="utf-8")
    print(f"Found {len(found)} official city records. Wrote {OUT_FILE.relative_to(REPO)}.")
    return 0 if found else 1


if __name__ == "__main__":
    sys.exit(main())
