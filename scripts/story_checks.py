"""
story_checks.py

ONE-TIME (run by hand from GitHub Actions: "Story fact checks").

Two outside checks for the story "Record Lows Are Disappearing", which counts
daily record lows in the official city records (unadjusted):

1. NOAA's ADJUSTED data. nClimDiv is NOAA's statewide monthly temperature
   series, corrected for station moves, instrument changes and observing-time
   changes. If the record-lows finding came from station quirks, it should
   weaken or vanish there. We apply the same test: for each state and calendar
   month, which year holds the coldest average low (and the warmest average
   high)? Do the years since 2000 hold their fair share?

2. STATION HISTORIES for the six places that set MORE record lows than chance
   (Austin Bergstrom, Hibbing, Jacksonville, Klamath Falls, Burns, Casper).
   For each, the weather stations near the record's site and the years each
   reported (from ACIS), and NOAA's station-history record (HOMR: moves,
   elevation changes) for the nearest long-running ones.

Every step catches its own errors, so one failure doesn't lose the rest.
Writes checks/story_checks.json. Takes a few minutes.

    python scripts/story_checks.py
"""

import json
import math
import re
import sys
import time
from collections import defaultdict
from datetime import date
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parents[1]
OUT_FILE = REPO / "checks" / "story_checks.json"
USER_AGENT = "(tallorderdigital.com weather-live, https://github.com/shawn-nafziger/weather-live)"
CLIMDIV_DIR = "https://www.ncei.noaa.gov/pub/data/cirs/climdiv/"
ACIS_META_URL = "https://data.rcc-acis.org/StnMeta"
HOMR_URLS = [
    "https://www.ncei.noaa.gov/access/homr/services/station/search?qid=GHCND:{id}&headersOnly=false",
    "https://www.ncei.noaa.gov/access/homr/api/v1/station/search?qid=GHCND:{id}&headersOnly=false",
]
SINCE = 2000
FIRST_YEAR = 1895
EXCEPTIONS = ["austin-bergstrom", "hibbing", "jacksonville", "klamath-falls", "burns", "casper"]
# For comparison: two records whose nights look like a city-to-airport move but
# which set FEWER record lows (station-move fingerprint, product plan §8).
CONTROLS = ["pittsburgh", "cincinnati"]
NEARBY_KM = 40
STATE_NAMES = ("Alabama Arizona Arkansas California Colorado Connecticut Delaware Florida Georgia Idaho Illinois "
               "Indiana Iowa Kansas Kentucky Louisiana Maine Maryland Massachusetts Michigan Minnesota Mississippi "
               "Missouri Montana Nebraska Nevada New_Hampshire New_Jersey New_Mexico New_York North_Carolina "
               "North_Dakota Ohio Oklahoma Oregon Pennsylvania Rhode_Island South_Carolina South_Dakota Tennessee "
               "Texas Utah Vermont Virginia Washington West_Virginia Wisconsin Wyoming").split()


# ---------------------------------------------------------------- nClimDiv

# NOAA's names for the statewide files: climdiv-tminst-..., climdiv-tmaxst-... (run #1, Nov 8, 2026,
# looked for "tmn"/"tmx" and found nothing). Both spellings are tried.
ELEMENT_NAMES = {"tmn": ("tmin", "tmn"), "tmx": ("tmax", "tmx")}


def latest_file(listing_html, element):
    """Newest 'climdiv-<element>st-v1.0.0-YYYYMMDD' named in the folder listing."""
    for name in ELEMENT_NAMES.get(element, (element,)):
        names = re.findall(rf"(climdiv-{name}st-v1\.0\.0-\d{{8}})", listing_html)
        if names:
            return max(names)
    seen = sorted(set(re.findall(r"climdiv-[a-z]+st-v[\d.]+-\d{8}", listing_html)))
    raise ValueError(f"no statewide {element} file in the NOAA folder listing; it lists: {seen[:20]}")


def parse_climdiv(text):
    """{state code: {year: [12 monthly values or None]}} from a climdiv *st file.

    Each line: a 10-digit code (state 3, division 1, element 2, year 4), then
    12 monthly values in °F; -99.90 is missing. Keeps the 48 contiguous states
    (codes 1-48) and the contiguous-US series (110)."""
    out = defaultdict(dict)
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 13 or len(parts[0]) != 10 or not parts[0].isdigit():
            continue
        code, year = int(parts[0][:3]), int(parts[0][6:10])
        if not (1 <= code <= 48 or code == 110):
            continue
        vals = [float(v) for v in parts[1:]]
        out[code][year] = [None if v <= -99 else v for v in vals]
    return out


def record_shares(series, cold, last_year):
    """For each calendar month, give the record (coldest if cold, else warmest)
    to the year(s) holding it, ties shared, and the chance share to every year
    with a value. Returns {year: [records, chance]} summed over the 12 months."""
    tally = defaultdict(lambda: [0.0, 0.0])
    for m in range(12):
        vals = {y: v[m] for y, v in series.items() if FIRST_YEAR <= y <= last_year and v[m] is not None}
        if len(vals) < 30:
            continue
        best = min(vals.values()) if cold else max(vals.values())
        holders = [y for y, v in vals.items() if v == best]
        for y in holders:
            tally[y][0] += 1 / len(holders)
        for y in vals:
            tally[y][1] += 1 / len(vals)
    return tally


def summarize(tallies):
    """Records and chance since SINCE, plus per decade, from {code: {year: [rec, chance]}}."""
    since = [0.0, 0.0]
    dec = defaultdict(lambda: [0.0, 0.0])
    for t in tallies.values():
        for y, (r, c) in t.items():
            d = dec[y // 10 * 10]
            d[0] += r; d[1] += c
            if y >= SINCE:
                since[0] += r; since[1] += c
    return {
        "records_since": round(since[0], 1), "chance_since": round(since[1], 1),
        "ratio_since": round(since[0] / since[1], 2) if since[1] else None,
        "decades": {str(k): {"records": round(v[0], 1), "chance": round(v[1], 1),
                             "ratio": round(v[0] / v[1], 2) if v[1] else None}
                    for k, v in sorted(dec.items())},
    }


def adjusted_check(session, today):
    listing = session.get(CLIMDIV_DIR, timeout=60).text
    out = {"source": CLIMDIV_DIR, "first_year": FIRST_YEAR}
    # Complete months only: a month counts once NOAA has published it, which the
    # missing-value code already handles, so the current year can be included.
    last_year = today.year
    for element, label, cold in (("tmn", "coldest_months_avg_low", True), ("tmx", "warmest_months_avg_high", False),
                                 ("tmn", "warmest_months_avg_low", False)):
        name = latest_file(listing, element)
        data = parse_climdiv(session.get(CLIMDIV_DIR + name, timeout=120).text)
        states = {c: record_shares(s, cold, last_year) for c, s in data.items() if c <= 48}
        res = summarize(states)
        res["file"] = name
        res["states"] = len(states)
        per_state = {}
        for c, t in states.items():
            r = sum(v[0] for y, v in t.items() if y >= SINCE)
            ch = sum(v[1] for y, v in t.items() if y >= SINCE)
            per_state[STATE_NAMES[c - 1].replace("_", " ")] = {"records": round(r, 1), "chance": round(ch, 1)}
        res["by_state"] = per_state
        if 110 in data:
            res["contiguous_us"] = summarize({110: record_shares(data[110], cold, last_year)})
        out[label] = res
        print(f"  adjusted {label}: {res['records_since']} since {SINCE} vs {res['chance_since']} by chance "
              f"({res['ratio_since']}), {res['states']} states")
    return out


# ---------------------------------------------------------------- station histories

def km(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * math.asin(math.sqrt(a))


def ghcn_id(sids):
    """The GHCN-Daily id among an ACIS station's ids ('USW00013889 6' -> 'USW00013889')."""
    for s in sids or []:
        v, _, t = s.rpartition(" ")
        if t == "6":
            return v
    return None


def nearby_stations(session, city):
    lat, lon = city["lat"], city["lon"]
    bbox = f"{lon - 0.5:.3f},{lat - 0.4:.3f},{lon + 0.5:.3f},{lat + 0.4:.3f}"
    r = session.post(ACIS_META_URL, json={"bbox": bbox, "elems": "mint",
                                          "meta": "name,sids,ll,elev,valid_daterange"}, timeout=60)
    r.raise_for_status()
    out = []
    for m in r.json().get("meta", []):
        ll = m.get("ll")
        if not ll:
            continue
        d = km(lat, lon, ll[1], ll[0])
        if d > NEARBY_KM:
            continue
        rng = (m.get("valid_daterange") or [[]])[0]
        out.append({"name": m.get("name"), "ghcn": ghcn_id(m.get("sids")), "sids": m.get("sids"),
                    "km": round(d, 1), "elev_ft": m.get("elev"), "lat": ll[1], "lon": ll[0],
                    "from": rng[0] if rng else None, "to": rng[1] if len(rng) > 1 else None})
    return sorted(out, key=lambda s: (s["from"] or "9999"))


def homr(session, gid):
    """NOAA's station-history record: location periods with lat/lon/elevation."""
    last = None
    for url in HOMR_URLS:
        try:
            r = session.get(url.format(id=gid), timeout=60)
            if r.status_code == 200 and r.text.strip().startswith("{"):
                st = (r.json().get("stationCollection") or {}).get("stations") or []
                if not st:
                    return {"error": "no HOMR record"}
                s = st[0]
                locs = [{"from": l.get("date", {}).get("beginDate"), "to": l.get("date", {}).get("endDate"),
                         "lat": (l.get("latitude") or ""), "lon": (l.get("longitude") or ""),
                         "elev": [e.get("value") for e in (l.get("elevations") or [])][:1]}
                        for l in (s.get("location") or {}).get("geoInfo", [])
                        if isinstance(l, dict)] if isinstance(s.get("location"), dict) else []
                return {"url": url.format(id=gid), "names": [n.get("name") for n in s.get("names", [])][:8],
                        "locations": locs, "raw_location": s.get("location") if not locs else None}
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001
            last = str(e)[:200]
    return {"error": last}


def station_check(session, cities):
    out = {}
    for slug in EXCEPTIONS + CONTROLS:
        city = cities.get(slug)
        if not city:
            out[slug] = {"error": "not in config"}
            continue
        entry = {"name": city["name"], "record_sid": city["record_sid"], "record_name": city.get("record_name"),
                 "lat": city["lat"], "lon": city["lon"], "exception": slug in EXCEPTIONS}
        try:
            near = nearby_stations(session, city)
            entry["nearby"] = near
            # Station histories for the long-running ones: started before 1960, or the closest three.
            pick = [s for s in near if s["ghcn"] and (s["from"] or "9999") < "1960"]
            pick += [s for s in sorted(near, key=lambda s: s["km"]) if s["ghcn"] and s not in pick][:3]
            entry["histories"] = {}
            for s in pick[:6]:
                entry["histories"][s["ghcn"]] = homr(session, s["ghcn"])
                time.sleep(0.5)
        except Exception as e:  # noqa: BLE001
            entry["error"] = str(e)[:300]
        out[slug] = entry
        print(f"  {city['name']}: {len(entry.get('nearby', []))} stations nearby"
              + (f", ERROR {entry['error']}" if "error" in entry else ""))
    return out


def load_cities():
    out = {}
    for f in ("cities.json", "hourly_metros.json", "almanac_cities.json"):
        c = json.loads((REPO / "config" / f).read_text(encoding="utf-8"))
        rows = c.get("cities", c.get("metros", [])) if isinstance(c, dict) else c
        if isinstance(rows, dict):
            rows = [dict(v, slug=k) for k, v in rows.items() if not k.startswith("_")]
        for r in rows:
            if isinstance(r, dict) and "slug" in r and "record_sid" in r and "lat" in r:
                out[r["slug"]] = r
    return out


def main():
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    today = date.today()
    result = {"run": today.isoformat(), "since": SINCE}
    print("1. NOAA adjusted statewide data (nClimDiv)")
    try:
        result["adjusted"] = adjusted_check(session, today)
    except Exception as e:  # noqa: BLE001
        result["adjusted"] = {"error": str(e)[:300]}
        print("  ERROR", e)
    print("2. Station histories")
    result["stations"] = station_check(session, load_cities())
    OUT_FILE.parent.mkdir(exist_ok=True)
    OUT_FILE.write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"Saved {OUT_FILE.relative_to(REPO)}")
    if "error" in result["adjusted"] and all("error" in v for v in result["stations"].values()):
        sys.exit("Both checks failed; see the errors above.")


if __name__ == "__main__":
    main()
