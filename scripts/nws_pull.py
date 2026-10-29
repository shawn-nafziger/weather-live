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
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

REPO = Path(__file__).resolve().parents[1]
CITIES_FILE = REPO / "config" / "cities.json"
ARCHIVE_DIR = REPO / "archive"
FORECAST_DIR = ARCHIVE_DIR / "forecasts"
OBSERVATION_DIR = ARCHIVE_DIR / "observations"
LIVE_DIR = REPO / "live"

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
]


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
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
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

def today_so_far(rows, tz, now):
    """High and low among today's readings, in the city's local day."""
    local_today = now.astimezone(tz).date()
    today = [r for r in rows
             if r["TempC"] is not None and r["TempQC"] not in ("X", "Q")
             and parse_time(r["TimestampUTC"]).astimezone(tz).date() == local_today]
    summary = {"date_local": local_today.isoformat(), "readings": len(today),
               "note": "So far, from hourly readings. The official high and low are posted the next morning and can differ by a degree or two."}
    if today:
        hi = max(today, key=lambda r: r["TempC"])
        lo = min(today, key=lambda r: r["TempC"])
        summary.update({
            "high_c": hi["TempC"], "high_f": c_to_f(hi["TempC"]), "high_time_utc": hi["TimestampUTC"],
            "low_c": lo["TempC"], "low_f": c_to_f(lo["TempC"]), "low_time_utc": lo["TimestampUTC"],
        })
    return summary


def latest_reading(rows):
    for r in reversed(rows):
        if r["TempC"] is not None and r["TempQC"] not in ("X", "Q"):
            return {"time_utc": r["TimestampUTC"], "temp_c": r["TempC"], "temp_f": c_to_f(r["TempC"]),
                    "dewpoint_c": r["DewpointC"], "humidity_pct": r["RelativeHumidityPct"],
                    "wind_kmh": r["WindSpeedKmh"], "description": r["Description"]}
    return None


def run_city(session, city, now):
    where = resolve_city(session, city)
    issued, forecast_rows = pull_forecast(session, city, where, now)
    new_forecast_rows = save_forecast(city, issued, forecast_rows, now)
    obs = pull_observations(session, city, now)
    new_obs = save_observations(obs)
    hourly = pull_hourly(session, where, now)

    live = {
        "city": city["name"], "state": city["state"], "slug": city["slug"],
        "location_key": city["location_key"], "time_zone": city["time_zone"],
        "station": {"id": city["station"], "name": where["station_name"],
                    "lat": where["lat"], "lon": where["lon"], "history": city["history"]},
        "as_of_utc": iso_utc(now),
        "now": latest_reading(obs),
        "today_so_far": today_so_far(obs, ZoneInfo(city["time_zone"]), now),
        "forecast": {
            "issued_utc": iso_utc(issued), "grid": where["grid"],
            "periods": [{"name": r["PeriodName"], "start": r["StartLocal"], "is_daytime": r["IsDaytime"],
                         "temp_f": r["TempF"], "temp_c": r["TempC"],
                         "precip_chance_pct": r["PrecipChancePct"], "short_forecast": r["ShortForecast"]}
                        for r in forecast_rows],
        },
        "hourly": hourly,
        "source": "National Weather Service (api.weather.gov), public domain",
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
