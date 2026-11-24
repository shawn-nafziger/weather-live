"""
forecast.py: the 7-day forecast strip at the top of each Almanac overview page (added Nov 21, 2026).

Two halves:
  * fetch_all()  asks the National Weather Service for every place's 7-day forecast, at the official record's
                 own location, and writes one small JSON per place into a work folder. Run by the daily job just
                 before the pages are built (scripts/forecast_pull.py). Nothing is committed: the forecasts are
                 written into the pages and the folder is thrown away, so the repo doesn't grow.
                 (The full forecast history is still archived hourly for the 53 places in cities.json and
                 hourly_metros.json, for the forecast-accuracy story; that's nws_pull.py, unchanged.)
  * strip_html() turns one place's forecast into the strip: each day's forecast high, that night's low, the
                 chance of precipitation and a few words, plus where a notable forecast would land in the same
                 official record the page shows (Shawn, Nov 21: "every place").

Wording rules (weather-live CLAUDE.md): forecast-based statements say "forecast" and "would"; ranks are years
strictly warmer + 1 with ties shared, and only the warmest or coolest fifth gets ranked wording.
"""
import html
import json
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

API = "https://api.weather.gov"
USER_AGENT = "(tallorderdigital.com weather-live, https://github.com/shawn-nafziger/weather-live)"
TIMEOUT_SECONDS = 30
RETRIES = 3
WORKERS = 4                 # polite to the NWS: a few requests at a time
MAX_AGE_HOURS = 36          # an older forecast isn't shown (a stale strip is worse than none)
LESS_CERTAIN_FROM_DAY = 6   # days 6 and 7 are marked "less certain"
SHORT_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


# ------------------------------------------------------------------ fetching (network; run by the daily job)
def _get(session, url):
    last = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = session.get(url, timeout=TIMEOUT_SECONDS)
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()
            last = f"HTTP {r.status_code}"
        except Exception as e:  # noqa: BLE001  (requests errors; a 4xx is raised by raise_for_status)
            last = f"{type(e).__name__}: {e}"
            resp = getattr(e, "response", None)
            if resp is not None and resp.status_code < 500:
                raise
        if attempt < RETRIES:
            time.sleep(3 * attempt)
    raise RuntimeError(last)


def fetch_one(session, slug, lat, lon):
    point = _get(session, f"{API}/points/{lat:.4f},{lon:.4f}")["properties"]
    fc = _get(session, point["forecast"])["properties"]
    return {
        "slug": slug,
        "issued_utc": fc.get("updateTime") or fc.get("generatedAt"),
        "time_zone": point["timeZone"],
        "grid": f"{point['gridId']}/{point['gridX']},{point['gridY']}",
        "periods": [{
            "name": p["name"], "start": p["startTime"], "is_daytime": p["isDaytime"],
            "temp_f": p["temperature"] if p.get("temperatureUnit", "F") == "F" else round(p["temperature"] * 9 / 5 + 32),
            "precip_pct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
            "short": p["shortForecast"],
        } for p in fc["periods"]],
    }


def fetch_all(places, out_dir):
    """places: {slug: (lat, lon)}. Writes out_dir/<slug>.json; returns (ok, {slug: error})."""
    import requests
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/geo+json"})

    def one(item):
        slug, (lat, lon) = item
        try:
            data = fetch_one(session, slug, lat, lon)
            (out_dir / f"{slug}.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
            return slug, None
        except Exception as e:  # noqa: BLE001  one place's failure never stops the others
            return slug, f"{type(e).__name__}: {e}"[:200]

    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(one, sorted(places.items())))
    failed = {s: e for s, e in results if e}
    return len(results) - len(failed), failed


def load(folder, slug):
    if not folder:
        return None
    p = Path(folder) / f"{slug}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


# ------------------------------------------------------------------ the strip
def _ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def days(fc, now=None):
    """The forecast as up to 7 local dates: {date, label, high, low, precip, short}. The low is the night that
    follows the day (as weather apps show it); a night with no daytime period before it (the forecast issued in
    the evening) gets its own row with only a low."""
    tz = ZoneInfo(fc["time_zone"])
    out = {}
    for p in fc["periods"]:
        start = datetime.fromisoformat(p["start"]).astimezone(tz)
        d = start.date() if p["is_daytime"] or start.hour >= 12 else start.date() - timedelta(days=1)
        row = out.setdefault(d, {"date": d, "label": None, "high": None, "low": None, "precip": None, "short": None})
        if p["is_daytime"]:
            row.update(high=p["temp_f"], short=p["short"], label=p["name"])
        else:
            row["low"] = p["temp_f"]
            row["short"] = row["short"] or p["short"]
            row["label"] = row["label"] or p["name"]
        if p["precip_pct"] is not None:
            row["precip"] = max(row["precip"] or 0, p["precip_pct"])
    return [out[d] for d in sorted(out)][:7]


def notable_line(row, hi_by_md, lo_by_md, rank_fn, year):
    """The one thing worth saying about a forecast day, or None. Highs rank against that date's highs; the
    night's low lands on the next morning, so it ranks against the next date's lows (the official low of
    a day is usually set near dawn)."""
    cands = []
    d = row["date"]
    md = d.strftime("%m-%d")
    if row["high"] is not None and hi_by_md.get(md):
        vals = {y: v for y, v in hi_by_md[md].items() if y != year}
        if len(vals) >= 30:
            r = rank_fn({**vals, year: row["high"]}, year, row["high"])
            if r["notable"]:
                cands.append(("high", d, r, vals))
    nd = d + timedelta(days=1)
    nmd = nd.strftime("%m-%d")
    if row["low"] is not None and lo_by_md.get(nmd):
        vals = {y: v for y, v in lo_by_md[nmd].items() if y != nd.year}
        if len(vals) >= 30:
            r = rank_fn({**vals, nd.year: row["low"]}, nd.year, row["low"])
            if r["notable"]:
                cands.append(("low", nd, r, vals))
    if not cands:
        return None
    kind, when, r, vals = min(cands, key=lambda c: (c[2]["rank"], 0 if c[0] == "high" else 1))
    word = {("high", "warm"): "warmest", ("high", "cool"): "coolest",
            ("low", "warm"): "warmest", ("low", "cool"): "coldest"}[(kind, r["standing"])]
    date_words = f"{SHORT_MONTHS[when.month - 1]} {when.day}"
    # Wording (Shawn, Nov 24: not "high of 134"): "Would be the 2nd warmest Nov 21 high in 134 years". The count is
    # every year with a value for that date, this one included, as everywhere on the Almanac.
    lead = "Would" if kind == "high" else "Low that night would"
    if r["record"]:
        rec = max(vals.values()) if r["standing"] == "warm" else min(vals.values())
        rec_years = sorted(y for y, v in vals.items() if v == rec)
        what = "record high" if (kind, r["standing"]) == ("high", "warm") else \
               "record low" if (kind, r["standing"]) == ("low", "cool") else f"record {word} {kind}"
        verb = "tie" if r["tied"] else "beat"
        return {"kind": kind, "record": True,
                "text": f"{lead} {verb} the {date_words} {what} ({_deg(rec)}, {rec_years[-1]})"}
    verb = "tie for" if r["tied"] else "be"
    return {"kind": kind, "record": False,
            "text": f"{lead} {verb} the {_ordinal(r['rank'])} {word} {date_words} {kind} in {r['years']} years"}


def _deg(v):
    return f"{'−' if v < 0 else ''}{abs(v)}°F"


# ------------------------------------------------------------------ weather icons (Nov 24, 2026)
# Small line drawings in the text color, picked from the NWS's few words ("Chance Rain Showers", "Mostly Sunny").
# Drawn here (no icon library or font): 24×24 strokes. Decorative: the words beside them carry the meaning.
_SUN = '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>'
_MOON = '<path d="M19 14.5A7.5 7.5 0 0 1 9.5 5a7.5 7.5 0 1 0 9.5 9.5z"/>'
_CLOUD = '<path d="M7 18h10a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7 9.5 4.25 4.25 0 0 0 7 18z"/>'
_CLOUD_HI = '<path d="M7 15h10a4 4 0 0 0 .6-7.95A5.5 5.5 0 0 0 7 6.5 4.25 4.25 0 0 0 7 15z"/>'
_PARTLY = ('<circle cx="8.5" cy="8.5" r="3"/><path d="M8.5 2.5v1.5M2.5 8.5H4M4.3 4.3l1 1M12.7 4.3l-1 1"/>'
           '<path d="M9 19h8.5a3.5 3.5 0 0 0 .5-6.96A4.75 4.75 0 0 0 9 11.5a3.75 3.75 0 0 0 0 7.5z"/>')
_PARTLY_NIGHT = ('<path d="M11.5 8.2A4.5 4.5 0 0 1 6 3a4.5 4.5 0 1 0 5.5 5.2z"/>'
                 '<path d="M9 19h8.5a3.5 3.5 0 0 0 .5-6.96A4.75 4.75 0 0 0 9 11.5a3.75 3.75 0 0 0 0 7.5z"/>')
ICONS = {
    "sun": _SUN, "moon": _MOON, "partly": _PARTLY, "partly-night": _PARTLY_NIGHT, "cloud": _CLOUD,
    "rain": _CLOUD_HI + '<path d="M8 18l-1 3M12 18l-1 3M16 18l-1 3"/>',
    "storm": _CLOUD_HI + '<path d="M12.5 16l-2 3.5h3l-2 3.5"/>',
    "snow": _CLOUD_HI + '<path d="M8 19v.01M12 18v.01M16 19v.01M10 21.5v.01M14 21.5v.01" stroke-width="2.4"/>',
    "mix": _CLOUD_HI + '<path d="M8 18l-1 3M16 18l-1 3"/><path d="M12 19.5v.01" stroke-width="2.4"/>',
    "fog": '<path d="M4 9h16M3 13h18M5 17h14"/>',
    "wind": '<path d="M3 9h11a3 3 0 1 0-3-3M3 14h15a3 3 0 1 1-3 3M3 19h7"/>',
}


def icon_name(words, night=False):
    w = (words or "").lower()
    for keys, name in ((("thunder", "t-storm", "tstorm"), "storm"),
                       (("snow", "flurr", "blizzard"), "snow"),
                       (("sleet", "freezing", "ice", "wintry", "mix"), "mix"),
                       (("rain", "shower", "drizzle"), "rain"),
                       (("fog", "haze", "smoke", "dust", "mist"), "fog"),
                       (("mostly cloudy", "overcast"), "cloud"),
                       (("partly", "mostly sunny", "mostly clear"), "partly"),
                       (("cloudy",), "cloud"),
                       (("sunny", "clear", "fair", "hot", "cold"), "sun"),
                       (("wind", "breezy", "blustery"), "wind")):
        if any(k in w for k in keys):
            if night and name in ("sun", "partly"):
                return "moon" if name == "sun" else "partly-night"
            return name
    return None


def icon(words, night=False):
    name = icon_name(words, night)
    if not name:
        return ""
    return (f'<svg class="fc-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false" fill="none" '
            f'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">{ICONS[name]}</svg>')


def strip_html(fc, h, rank_fn, now=None, city_name=""):
    """The strip as HTML, or "" when there's no usable forecast (missing, or older than MAX_AGE_HOURS)."""
    if not fc or not fc.get("periods"):
        return ""
    now = now or datetime.now(timezone.utc)
    issued = datetime.fromisoformat(fc["issued_utc"].replace("Z", "+00:00"))
    if now - issued > timedelta(hours=MAX_AGE_HOURS):
        return ""
    tz = ZoneInfo(fc["time_zone"])
    rows = days(fc)
    if not rows:
        return ""
    hi_by_md, lo_by_md = {}, {}
    for md, y, hi, lo in zip(h["MD"], h["Y"], h["Hi"], h["Lo"]):
        if hi == hi:   # not NaN
            hi_by_md.setdefault(md, {})[int(y)] = int(hi)
        if lo == lo:
            lo_by_md.setdefault(md, {})[int(y)] = int(lo)
    local = issued.astimezone(tz)
    issued_words = (f"{SHORT_MONTHS[local.month - 1]} {local.day}, "
                    f"{local.strftime('%I:%M %p').lstrip('0')} {local.tzname()}")
    cells = []
    for i, row in enumerate(rows, start=1):
        d = row["date"]
        note = notable_line(row, hi_by_md, lo_by_md, rank_fn, d.year)
        hi = f'{"−" if row["high"] < 0 else ""}{abs(row["high"])}°' if row["high"] is not None else "—"
        lo = f'{"−" if row["low"] < 0 else ""}{abs(row["low"])}°' if row["low"] is not None else "—"
        label = {"Today": "Today", "This Afternoon": "Today", "Tonight": "Tonight"}.get(row["label"], d.strftime("%a"))
        precip = f'{row["precip"]}%' if row["precip"] else "0%"
        cls = "fc-day" + (" fc-later" if i >= LESS_CERTAIN_FROM_DAY else "") + (" fc-note-rec" if note and note["record"] else "")
        cells.append(
            f'<li class="{cls}">'
            f'<p class="fc-when"><b>{html.escape(label)}</b> <span>{SHORT_MONTHS[d.month - 1]} {d.day}</span></p>'
            f'<p class="fc-temps"><span class="fc-hi" aria-label="Forecast high {hi}">{hi}</span>'
            f'<span class="fc-lo" aria-label="Low that night {lo}">{lo}</span></p>'
            f'<p class="fc-sky">{icon(row["short"], night=row["high"] is None)}<span>{html.escape(row["short"] or "")}</span></p>'
            f'<p class="fc-pop">Precip. {precip}</p>'
            + (f'<p class="fc-note">{html.escape(note["text"])}</p>' if note else "")
            + ('<p class="fc-later-tag">Less certain</p>' if i >= LESS_CERTAIN_FROM_DAY else "")
            + "</li>")
    return (
        f'<section class="forecast" id="forecast" aria-labelledby="fc-h" data-issued="{issued.isoformat()}">'
        f'<h2 class="fc-title" id="fc-h">{html.escape(city_name)} 7-day forecast</h2>'
        f'<p class="fc-meta">Forecast high and the low that night, °F. National Weather Service forecast for the '
        f'record\'s location, issued {html.escape(issued_words)}. Notes say where a forecast would rank in this '
        f'official record. Days 6 and 7 are less certain.</p>'
        f'<ol class="fc-days">{"".join(cells)}</ol>'
        # If the site hasn't been rebuilt for a while, hide a forecast that has gone stale rather than show it.
        f'<script>(function(){{var s=document.getElementById("forecast");'
        f'if(Date.now()-Date.parse(s.dataset.issued)>{MAX_AGE_HOURS}*3600e3)s.hidden=true;}})();</script>'
        '</section>')
