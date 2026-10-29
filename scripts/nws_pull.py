"""
nws_pull.py

HOURLY (run by GitHub Actions, see .github/workflows/hourly.yml).

For each city in config/cities.json it pulls from the National Weather Service:
  1. the 7-day forecast, saved to archive/forecasts/ under its issue time
     and never overwritten (this is what forecast accuracy is measured from);
  2. the station's recent hourly readings, saved to archive/observations/
     (duplicates skipped);
  3. a small "latest" file per city in live/, which the live page will read:
     current conditions, today's high and low so far, and the forecast.

One city failing doesn't stop the others; live/status.json records what
happened. The run fails only if every city fails.

    python scripts/nws_pull.py
"""

import csv
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

import ranks

REPO = Path(__file__).resolve().parents[1]
CITIES_FILE = REPO / "config" / "cities.json"
ARCHIVE_DIR = REPO / "archive"
FORECAST_DIR = ARCHIVE_DIR / "forecasts"
OBSERVATION_DIR = ARCHIVE_DIR / "observations"
LIVE_DIR = REPO / "live"
HISTORY_DIR = REPO / "history"     # official daily records, kept by station_update.py

API = "https://api.weather.gov"
# NWS asks every caller to identify itself. A site address, no personal email.
USER_AGENT = "(tallorderdigital.com weather-live, https://github.com/shawn-nafziger/weather-live)"
TIMEOUT_SECONDS = 30
RETRIES = 3                 # NWS often answers 500/503 briefly; try again
RETRY_PAUSE_SECONDS = 4
OBSERVATION_LOOKBACK_HOURS = 30   # re-read the last 30 h each run, so a missed run leaves no gap
HOURLY_FORECAST_HOURS = 48        # how much of the hourly forecast the live file carries

FORECAST_COLUMNS = [
    "City", "Station", "IssuedUTC", "GeneratedUTC", "RetrievedUTC", "Grid",
    "PeriodNumber", "PeriodName", "StartLocal", "EndLocal", "IsDaytime",
    "TempF", "TempC", "PrecipChancePct", "WindSpeed", "WindDirection", "ShortForecast",
]
OBSERVATION_COLUMNS = [
    "City", "Station", "TimestampUTC", "TempC", "TempQC", "DewpointC", "RelativeHumidityPct",
    "WindDirectionDeg", "WindSpeedKmh", "WindGustKmh", "PressurePa", "PrecipLastHourMM",
    "Max24hC", "Min24hC", "Description", "RetrievedUTC",
    "ReportType", "Precise", "RawMessage",
]
# A METAR temperature group like "T02060089" (20.6 °C, 8.9 °C) means tenths of a degree.
# Stations also send 5-minute readings with no METAR, rounded to whole °C.
T_GROUP = re.compile(r"\bT[01]\d{3}(?:[01]\d{3})?\b")


# =====================================================
# HTTP
# =====================================================

def make_session():
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/geo+json"})
    return s


def get_json(session, url, params=None):
    """GET with a few retries for the NWS's frequent short outages."""
    last_error = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.get(url, params=params, timeout=TIMEOUT_SECONDS)
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()
            last_error = f"HTTP {r.status_code} from {url}"
        except requests.RequestException as e:
            last_error = f"{type(e).__name__}: {e}"
            if getattr(e, "response", None) is not None and e.response.status_code < 500:
                raise
        if attempt < RETRIES:
            time.sleep(RETRY_PAUSE_SECONDS * attempt)
    raise RuntimeError(last_error)


# =====================================================
# HELPERS
# =====================================================

def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")) if text else None


def f_to_c(f):
    return None if f is None else round((f - 32) * 5 / 9, 2)


def c_to_f(c):
    return None if c is None else round(c * 9 / 5 + 32, 1)


def qv(props, key, to=None):
    """A quantitative NWS value ({'value': x, 'unitCode': ...}) converted to plain units."""
    item = props.get(key) or {}
    value = item.get("value")
    if value is None:
        return None
    unit = item.get("unitCode", "")
    if to == "c" and unit.endswith("degF"):
        value = (value - 32) * 5 / 9
    return round(value, 2)


def read_csv_keys(path, key_fields):
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as f:
        return {tuple(row[k] for k in key_fields) for row in csv.DictReader(f)}


def append_rows(path, columns, rows):
    """Append rows to a CSV, writing the header when the file is new."""
    if not rows:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    if not new_file:
        # Keep an existing file's own columns: a day file started before a
        # column was added stays consistent; the new column starts next day.
        with path.open(newline="", encoding="utf-8") as f:
            columns = next(csv.reader(f))
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)
    return len(rows)


def day_file(folder, day):
    """archive/<kind>/YYYY/MM/YYYY-MM-DD.csv: past days are never touched again."""
    return folder / f"{day:%Y}" / f"{day:%m}" / f"{day:%Y-%m-%d}.csv"


# =====================================================
# PULLS
# =====================================================

def resolve_city(session, city):
    """The station's location, then the forecast grid for that exact spot."""
    station = get_json(session, f"{API}/stations/{city['station']}")
    lon, lat = station["geometry"]["coordinates"][:2]
    point = get_json(session, f"{API}/points/{lat:.4f},{lon:.4f}")["properties"]
    return {
        "station_name": station["properties"].get("name"),
        "lat": round(lat, 4), "lon": round(lon, 4),
        "grid": f"{point['gridId']}/{point['gridX']},{point['gridY']}",
        "forecast_url": point["forecast"],
        "hourly_url": point["forecastHourly"],
    }


def pull_forecast(session, city, where, retrieved):
    data = get_json(session, where["forecast_url"])["properties"]
    issued = parse_time(data.get("updateTime") or data.get("generatedAt"))
    generated = parse_time(data.get("generatedAt"))
    rows = []
    for p in data["periods"]:
        temp_f = p.get("temperature")
        if p.get("temperatureUnit") == "C":
            temp_f = c_to_f(temp_f)
        rows.append({
            "City": city["slug"], "Station": city["station"],
            "IssuedUTC": iso_utc(issued), "GeneratedUTC": iso_utc(generated) if generated else "",
            "RetrievedUTC": iso_utc(retrieved), "Grid": where["grid"],
            "PeriodNumber": p["number"], "PeriodName": p["name"],
            "StartLocal": p["startTime"], "EndLocal": p["endTime"], "IsDaytime": p["isDaytime"],
            "TempF": temp_f, "TempC": f_to_c(temp_f),
            "PrecipChancePct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
            "WindSpeed": p.get("windSpeed"), "WindDirection": p.get("windDirection"),
            "ShortForecast": p.get("shortForecast"),
        })
    return issued, rows


def save_forecast(city, issued, rows, retrieved):
    """Save this issue unless it's already archived (NWS reissues every few hours)."""
    path = day_file(FORECAST_DIR, retrieved)
    # An issue can only already exist in today's or yesterday's file.
    seen = set()
    for d in (retrieved, retrieved - timedelta(days=1)):
        seen |= read_csv_keys(day_file(FORECAST_DIR, d), ["City", "IssuedUTC"])
    if (city["slug"], iso_utc(issued)) in seen:
        return 0
    return append_rows(path, FORECAST_COLUMNS, rows)


def pull_hourly(session, where, now):
    data = get_json(session, where["hourly_url"])["properties"]
    out = []
    for p in data["periods"]:
        start = parse_time(p["startTime"])
        if start + timedelta(hours=1) <= now:
            continue
        if len(out) >= HOURLY_FORECAST_HOURS:
            break
        temp_f = p.get("temperature") if p.get("temperatureUnit") != "C" else c_to_f(p.get("temperature"))
        out.append({
            "start": p["startTime"], "temp_f": temp_f, "temp_c": f_to_c(temp_f),
            "precip_chance_pct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
            "short_forecast": p.get("shortForecast"),
        })
    return {"issued_utc": iso_utc(parse_time(data.get("updateTime") or data.get("generatedAt"))), "hours": out}


def pull_observations(session, city, now):
    start = now - timedelta(hours=OBSERVATION_LOOKBACK_HOURS)
    data = get_json(session, f"{API}/stations/{city['station']}/observations",
                    params={"start": iso_utc(start)})
    rows = []
    for feat in data.get("features", []):
        p = feat["properties"]
        ts = parse_time(p["timestamp"])
        raw = (p.get("rawMessage") or "").strip()
        rows.append({
            "City": city["slug"], "Station": city["station"], "TimestampUTC": iso_utc(ts),
            "TempC": qv(p, "temperature", "c"),
            "TempQC": (p.get("temperature") or {}).get("qualityControl", ""),
            "DewpointC": qv(p, "dewpoint", "c"),
            "RelativeHumidityPct": qv(p, "relativeHumidity"),
            "WindDirectionDeg": qv(p, "windDirection"),
            "WindSpeedKmh": qv(p, "windSpeed"), "WindGustKmh": qv(p, "windGust"),
            "PressurePa": qv(p, "barometricPressure"),
            "PrecipLastHourMM": qv(p, "precipitationLastHour"),
            "Max24hC": qv(p, "maxTemperatureLast24Hours", "c"),
            "Min24hC": qv(p, "minTemperatureLast24Hours", "c"),
            "Description": p.get("textDescription") or "",
            "RetrievedUTC": iso_utc(now),
            "ReportType": "METAR" if raw else "5-minute",
            "Precise": bool(T_GROUP.search(raw)),
            "RawMessage": raw,
        })
    rows.sort(key=lambda r: r["TimestampUTC"])
    return rows


def save_observations(rows):
    """Each reading goes in the file for its own UTC date; duplicates skipped."""
    by_day = {}
    for r in rows:
        by_day.setdefault(parse_time(r["TimestampUTC"]).date(), []).append(r)
    added = 0
    for day, day_rows in by_day.items():
        path = day_file(OBSERVATION_DIR, day)
        seen = read_csv_keys(path, ["Station", "TimestampUTC"])
        fresh = [r for r in day_rows if (r["Station"], r["TimestampUTC"]) not in seen]
        added += append_rows(path, OBSERVATION_COLUMNS, fresh)
    return added


# =====================================================
# LIVE SUMMARY
# =====================================================

def usable(r):
    return r["TempC"] is not None and r["TempQC"] not in ("X", "Q")


def today_so_far(rows, tz, now):
    """Today's high and low so far, in the city's local day.

    Uses the station's hourly reports (METARs, tenths of a degree). The
    5-minute readings are rounded to whole °C, so they're used only to flag a
    peak that certainly happened between hourly reports. Stations with no
    hourly reports (e.g. some small airports) fall back to the 5-minute
    readings, marked approximate.
    """
    local_today = now.astimezone(tz).date()
    today = [r for r in rows if usable(r)
             and parse_time(r["TimestampUTC"]).astimezone(tz).date() == local_today]
    precise = [r for r in today if r["Precise"]]
    rounded = [r for r in today if not r["Precise"]]
    base = precise or today
    summary = {
        "date_local": local_today.isoformat(),
        "readings": len(base),
        "basis": "hourly reports (tenths of a degree)" if precise else "5-minute readings (whole °C, approximate)",
        "approximate": not precise,
        "note": "So far today. The official high and low, posted the next morning, come from "
                "continuous readings and can differ by a degree or two.",
    }
    if not base:
        return summary
    hi = max(base, key=lambda r: r["TempC"])
    lo = min(base, key=lambda r: r["TempC"])
    summary.update({
        "high_c": hi["TempC"], "high_f": c_to_f(hi["TempC"]), "high_time_utc": hi["TimestampUTC"],
        "low_c": lo["TempC"], "low_f": c_to_f(lo["TempC"]), "low_time_utc": lo["TimestampUTC"],
    })
    if precise and rounded:
        # A whole-degree reading X means somewhere from X-0.5 to X+0.5 °C.
        top = max(rounded, key=lambda r: r["TempC"])
        if top["TempC"] - 0.5 > hi["TempC"]:
            summary["peak_between_reports"] = {
                "at_least_f": c_to_f(top["TempC"] - 0.5), "about_f": c_to_f(top["TempC"]),
                "time_utc": top["TimestampUTC"]}
        bottom = min(rounded, key=lambda r: r["TempC"])
        if bottom["TempC"] + 0.5 < lo["TempC"]:
            summary["dip_between_reports"] = {
                "at_most_f": c_to_f(bottom["TempC"] + 0.5), "about_f": c_to_f(bottom["TempC"]),
                "time_utc": bottom["TimestampUTC"]}
    return summary


def latest_reading(rows):
    for r in reversed(rows):
        if usable(r):
            return {"time_utc": r["TimestampUTC"], "temp_c": r["TempC"], "temp_f": c_to_f(r["TempC"]),
                    "precision": "tenths of a degree" if r["Precise"] else "nearest whole °C",
                    "dewpoint_c": r["DewpointC"], "humidity_pct": r["RelativeHumidityPct"],
                    "wind_kmh": r["WindSpeedKmh"], "description": r["Description"]}
    return None


def ranked(history, day, value_f, field, kind):
    r = ranks.rank(history, day, value_f, field)
    return None if r is None else {**r, "phrase": ranks.phrase(r, day, kind)}


def record_context(city, history, so_far, forecast_rows, tz, now):
    """Yesterday's official values, and today/forecast highs against the record."""
    out = {"series": city["record_name"], "acis_id": city["record_sid"], "available": bool(history),
           "rank_today": city["rank_today"]}
    if not history:
        return out
    local_today = now.astimezone(tz).date()
    final_days = sorted(d for d, r in history.items() if r.get("MaxTempF") not in ("", None))
    if final_days:
        d = final_days[-1]
        row = history[d]
        out["latest_final"] = {
            "date": d.isoformat(), "is_yesterday": d == local_today - timedelta(days=1),
            "high_f": ranks.whole_f(row["MaxTempF"]),
            "low_f": ranks.whole_f(row["MinTempF"]) if row.get("MinTempF") not in ("", None) else None,
            "high_rank": ranked(history, d, row["MaxTempF"], "MaxTempF", "high"),
            "low_rank": ranked(history, d, row["MinTempF"], "MinTempF", "low")
            if row.get("MinTempF") not in ("", None) else None,
        }
    if not city["rank_today"]:
        out["note"] = ("Today's readings come from a different station than this record, "
                       "so today and the forecast aren't ranked against it.")
        return out
    if so_far.get("high_f") is not None:
        out["today_high_so_far"] = ranked(history, local_today, so_far["high_f"], "MaxTempF", "high")
    out["forecast_highs"] = []
    for r in forecast_rows:
        start = datetime.fromisoformat(r["StartLocal"]).date()
        if r["IsDaytime"] and r["TempF"] is not None and start >= local_today:
            out["forecast_highs"].append({"period": r["PeriodName"], "date": start.isoformat(),
                                          "high_f": r["TempF"],
                                          "would_rank": ranked(history, start, r["TempF"], "MaxTempF", "high")})
    return out


def run_city(session, city, now):
    where = resolve_city(session, city)
    issued, forecast_rows = pull_forecast(session, city, where, now)
    new_forecast_rows = save_forecast(city, issued, forecast_rows, now)
    obs = pull_observations(session, city, now)
    new_obs = save_observations(obs)
    hourly = pull_hourly(session, where, now)
    tz = ZoneInfo(city["time_zone"])
    so_far = today_so_far(obs, tz, now)
    history = ranks.load_history(HISTORY_DIR / f"{city['slug']}.csv")

    live = {
        "city": city["name"], "state": city["state"], "slug": city["slug"],
        "location_key": city["location_key"], "time_zone": city["time_zone"],
        "station": {"id": city["station"], "name": where["station_name"],
                    "lat": where["lat"], "lon": where["lon"], "history": city["history"]},
        "as_of_utc": iso_utc(now),
        "now": latest_reading(obs),
        "today_so_far": so_far,
        "record": record_context(city, history, so_far, forecast_rows, tz, now),
        "forecast": {
            "issued_utc": iso_utc(issued), "grid": where["grid"],
            "periods": [{"name": r["PeriodName"], "start": r["StartLocal"], "is_daytime": r["IsDaytime"],
                         "temp_f": r["TempF"], "temp_c": r["TempC"],
                         "precip_chance_pct": r["PrecipChancePct"], "short_forecast": r["ShortForecast"]}
                        for r in forecast_rows],
        },
        "hourly": hourly,
        "source": "National Weather Service (api.weather.gov); official records from NOAA via RCC-ACIS. Public domain.",
    }
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    (LIVE_DIR / f"{city['slug']}.json").write_text(json.dumps(live, indent=1), encoding="utf-8")
    return {"forecast_issued_utc": iso_utc(issued), "new_forecast_rows": new_forecast_rows,
            "observations_read": len(obs), "new_observations": new_obs}


def main():
    cities = json.loads(CITIES_FILE.read_text(encoding="utf-8"))["cities"]
    session = make_session()
    now = utc_now()
    status = {"run_utc": iso_utc(now), "cities": {}}
    failures = 0
    for city in cities:
        try:
            result = run_city(session, city, now)
            status["cities"][city["slug"]] = {"ok": True, **result}
            print(f"{city['name']:<11} OK   forecast issued {result['forecast_issued_utc']}"
                  f" ({'saved' if result['new_forecast_rows'] else 'already saved'}),"
                  f" {result['new_observations']} new readings")
        except Exception as e:  # one city's failure shouldn't stop the rest
            failures += 1
            status["cities"][city["slug"]] = {"ok": False, "error": str(e)[:300]}
            print(f"{city['name']:<11} FAIL {e}")
    LIVE_DIR.mkdir(parents=True, exist_ok=True)
    (LIVE_DIR / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    print(f"{len(cities) - failures} of {len(cities)} cities updated.")
    return 1 if failures == len(cities) else 0


if __name__ == "__main__":
    sys.exit(main())
