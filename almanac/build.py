"""
Almanac: one page per city, built from the city's official daily record.

    python almanac/build.py goshen            one city, to site/almanac/goshen/
    python almanac/build.py --all             every city with a record, plus the index

Reads history/<city>.csv (kept current by scripts/station_update.py: NOAA via
RCC-ACIS, whole °F) and writes the pages into site/, which the daily workflow
publishes to weather.tallorderdigital.com (GitHub Pages). site/ is not committed.

Every sentence on a page is chosen from that city's own numbers. Before a page is
written, its claims are checked (ranks recounted, each change claim checked two
ways and against the last 40 years). If a check fails, that city's page is not
written and the others still are.
"""

import argparse
import calendar
import html
import json
import math
import re
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from albers import project

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
HISTORY_DIR = REPO / "history"
SITE_DIR = REPO / "site"
MAIN_SITE = "https://tallorderdigital.com/"


class ClaimError(Exception):
    """A sentence the page would print is not true for this city's data."""

COOP_CAVEAT = ("This is one volunteer station's raw record. Over a century, changes in observing time, instruments "
               "and exact location can shift readings by a degree or more; NOAA's adjusted climate datasets correct "
               "for these. Treat it as one station's record, and any single number as approximate.")
THREADEX_CAVEAT = ("This is the official city record as kept, without adjustments. It joins several observing sites "
                   "over the years, and a move, a new instrument or the city growing around a station can shift "
                   "readings (city growth mostly warms nights); NOAA's adjusted climate datasets correct for these. "
                   "Treat any single number as approximate.")

# The first seven places, with hand-written record descriptions. Every other place comes
# from config/almanac_cities.json (see load_config_cities below).
CITIES = {
    "goshen": {"lat": 41.5575, "lon": -85.8825, "name": "Goshen", "state": "Indiana", "st": "IN",
               # The nearest official city record; the page offers a switch to it.
               "pair": {"slug": "south-bend", "name": "South Bend", "miles": 27},
               "record_name": "GOSHEN 3SW (NOAA cooperative station 123418)",
               "record_kind": "a volunteer cooperative station read once a day, about 3 miles southwest of town",
               "caveat": COOP_CAVEAT, "kind": "coop"},
    "chicago": {"lat": 41.995, "lon": -87.934, "name": "Chicago", "state": "Illinois", "st": "IL",
                "record_name": "Chicago area official record (ThreadEx; O'Hare since 1980)",
                "record_kind": "the official long-term record NOAA keeps for the city, joining several sites over the years",
                "caveat": THREADEX_CAVEAT, "kind": "threadex"},
    "phoenix": {"lat": 33.428, "lon": -112.004, "name": "Phoenix", "state": "Arizona", "st": "AZ",
                "record_name": "Phoenix area official record (ThreadEx; Sky Harbor since 1933)",
                "record_kind": "the official long-term record NOAA keeps for the city, joining several sites over the years",
                "caveat": THREADEX_CAVEAT, "kind": "threadex"},
    "miami": {"lat": 25.788, "lon": -80.317, "name": "Miami", "state": "Florida", "st": "FL",
              "record_name": "Miami area official record (ThreadEx)",
              "record_kind": "the official long-term record NOAA keeps for the city, joining several sites over the years",
              "caveat": THREADEX_CAVEAT, "kind": "threadex"},
    "seattle": {"lat": 47.444, "lon": -122.314, "name": "Seattle", "state": "Washington", "st": "WA",
                "record_name": "Seattle area official record (ThreadEx; Sea-Tac)",
                "record_kind": "the official long-term record NOAA keeps for the city, joining several sites over the years",
                "caveat": THREADEX_CAVEAT, "kind": "threadex"},
    "south-bend": {"lat": 41.708, "lon": -86.316, "name": "South Bend", "state": "Indiana", "st": "IN",
                   "record_name": "South Bend area official record (ThreadEx; South Bend airport)",
                   "record_kind": "the official long-term record NOAA keeps for the city, joining several sites over the years",
                   "caveat": THREADEX_CAVEAT, "kind": "threadex"},
    "los-olivos": {"lat": 34.58223, "lon": -119.98159, "name": "Los Olivos", "state": "California", "st": "CA",
                   "record_name": "CACHUMA LAKE (NOAA cooperative station 041253)",
                   "record_kind": "a volunteer cooperative station read once a day, about 10 miles southeast of town",
                   "caveat": COOP_CAVEAT, "kind": "coop"},
}

THREADEX_KIND = "the official long-term record NOAA keeps for the city, joining several sites over the years"


def load_config_cities():
    """Add every place in config/almanac_cities.json that isn't already described above."""
    path = REPO / "config" / "almanac_cities.json"
    if not path.exists():
        return
    for c in json.loads(path.read_text(encoding="utf-8"))["cities"]:
        if c["slug"] in CITIES:
            continue
        threadex = "thr" in c["record_sid"]
        CITIES[c["slug"]] = {
            "lat": c["lat"], "lon": c["lon"], "name": c["name"], "state": c["state"], "st": c["st"],
            "record_name": c.get("record_name", c["name"]),
            "record_kind": THREADEX_KIND if threadex else "a volunteer cooperative station read once a day",
            "caveat": THREADEX_CAVEAT if threadex else COOP_CAVEAT, "kind": "threadex" if threadex else "coop"}


load_config_cities()

# Quality screen: a place is left off (and listed in the run log) if its record fails any of these.
STALE_DAYS = 10            # the latest final day must be at most this many days old
MAX_HIGH_F, MIN_LOW_F = 134, -80   # beyond the US all-time records (134 °F Death Valley; -80 °F Alaska)
MAX_LOW_ABOVE_HIGH = 0.005  # share of days whose low is above their high (data-entry errors)


class QualityError(ClaimError):
    """The record itself looks wrong or out of date; the page is not built."""


NORMALS = (1991, 2020)      # NOAA's current climate normals period
MAX_MISSING_PER_MONTH = 9   # a month counts if no more than 9 days are missing
NOTABLE_SHARE = 0.20        # same as weather-live/scripts/ranks.py
NOTABLE_MIN_RANK = 3
EDGE_SHARE = 0.80           # "at the edge": at least 80% of the way to clearing zero
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]


# ------------------------------------------------------------------ helpers
def whole_f(v):
    """Round half up to whole °F, as weather-live does."""
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else int(math.floor(float(v) + 0.5))


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def md_words(d):
    return f"{d:%B} {d.day}"


def same_date_rank(values, year, v):
    """values: {year: whole °F} for one calendar date (including `year`).
    Same rules as weather-live/scripts/ranks.py: rank = years strictly warmer + 1,
    ties share a rank, only the warmest or coolest fifth gets ranked wording."""
    others = {y: x for y, x in values.items() if y != year}
    n = len(others) + 1
    warm = sum(1 for x in others.values() if x > v) + 1
    cool = sum(1 for x in others.values() if x < v) + 1
    tied = sorted(y for y, x in others.items() if x == v)
    earlier = {y: x for y, x in others.items() if y < year}
    warm_since = max((y for y, x in earlier.items() if x >= v), default=None)
    cool_since = max((y for y, x in earlier.items() if x <= v), default=None)
    standing = "warm" if warm <= cool else "cool"
    k = warm if standing == "warm" else cool
    lower = sum(1 for x in others.values() if x < v)
    equal = len(tied)
    return {
        "value": v, "warm_rank": warm, "cool_rank": cool, "years": n,
        "first_year": min(min(others), year), "tied": tied, "standing": standing,
        "rank": k, "notable": k <= max(NOTABLE_MIN_RANK, round(n * NOTABLE_SHARE)),
        "since": warm_since if standing == "warm" else cool_since,
        "record": k == 1,
        # share of past same-date values below this one, ties counted as half (methods.md)
        "percentile": round(100 * (lower + 0.5 * equal) / len(others)),
    }


def rank_words(r, d, kind):
    """'4th warmest October 28 high (tied) of 108 since 1914' or 'typical for October 28'."""
    if not r["notable"]:
        return f"typical for {md_words(d)}"
    word = {("high", "warm"): "warmest", ("high", "cool"): "coolest",
            ("low", "warm"): "warmest", ("low", "cool"): "coldest"}[(kind, r["standing"])]
    if r["record"]:
        lead = "tied the record for" if r["tied"] else "the"
        return f"{lead} {word} {md_words(d)} {kind} on record ({r['years']} years since {r['first_year']})"
    tie = " (tied)" if r["tied"] else ""
    return f"{ordinal(r['rank'])} {word} {md_words(d)} {kind}{tie} of {r['years']} since {r['first_year']}"


def trend(series):
    """Straight-line trend through annual values, per decade, with its 95% range,
    and the signal / edge / noise class from methods.md."""
    s = series.dropna()
    x = s.index.values.astype(float)
    r = stats.linregress(x, s.values)
    half = stats.t.ppf(0.975, len(s) - 2) * r.stderr
    b = r.slope
    cls = "signal" if abs(b) > half else ("edge" if abs(b) >= EDGE_SHARE * half else "noise")
    return {"per_decade": round(b * 10, 3), "lo": round((b - half) * 10, 3), "hi": round((b + half) * 10, 3),
            "class": cls, "n": int(len(s)), "first": int(x.min()), "last": int(x.max()),
            "intercept": float(r.intercept), "slope": float(b),
            "total": round(b * (x.max() - x.min()), 1)}


def load(slug, path=None):
    h = pd.read_csv(path or HISTORY_DIR / f"{slug}.csv", dtype={"Date": str}, encoding="utf-8-sig")
    h["D"] = pd.to_datetime(h["Date"])
    h["Y"] = h["D"].dt.year
    h["M"] = h["D"].dt.month
    h["MD"] = h["Date"].str[5:10]
    h["Hi"] = h["MaxTempF"].map(whole_f)
    h["Lo"] = h["MinTempF"].map(whole_f)
    h["Pr"] = h["PrecipIn"]
    h["Sn"] = h["SnowIn"]
    return h.sort_values("D").reset_index(drop=True)


# ------------------------------------------------------------------ the numbers
def build(slug, path=None):
    city = CITIES[slug]
    h = load(slug, path)
    have = h.dropna(subset=["Hi", "Lo"])
    if have.empty:
        raise QualityError("no days with both a high and a low")
    asof = have["D"].max().date()
    # ---- quality screen ----
    problems = []
    if (date.today() - asof).days > STALE_DAYS:
        problems.append(f"latest day is {asof}, more than {STALE_DAYS} days old")
    if have["Hi"].max() > MAX_HIGH_F or have["Lo"].min() < MIN_LOW_F:
        problems.append(f"impossible values (high {have['Hi'].max():.0f}°F, low {have['Lo'].min():.0f}°F)")
    flipped = float((have["Lo"] > have["Hi"]).mean())
    if flipped > MAX_LOW_ABOVE_HIGH:
        problems.append(f"{flipped:.1%} of days have a low above the high")
    if problems:
        raise QualityError("; ".join(problems))
    yr = asof.year
    first_year = int(h["Y"].min())
    month_done = lambda y, m: (y, m) < (asof.year, asof.month) or asof == date(asof.year, asof.month,
                                                                                 calendar.monthrange(asof.year, asof.month)[1])

    # ---- 1. Yesterday: the latest final day, ranked against every same date ----
    md = asof.strftime("%m-%d")
    same = h[h["MD"] == md]
    hi_vals = {int(y): int(v) for y, v in zip(same["Y"], same["Hi"]) if not pd.isna(v)}
    lo_vals = {int(y): int(v) for y, v in zip(same["Y"], same["Lo"]) if not pd.isna(v)}
    y_hi, y_lo = hi_vals[yr], lo_vals[yr]
    r_hi = same_date_rank(hi_vals, yr, y_hi)
    r_lo = same_date_rank(lo_vals, yr, y_lo)
    rec_hi = max(v for y, v in hi_vals.items() if y != yr)
    rec_hi_years = sorted(y for y, v in hi_vals.items() if v == rec_hi and y != yr)
    rec_lo = min(v for y, v in lo_vals.items() if y != yr)
    rec_lo_years = sorted(y for y, v in lo_vals.items() if v == rec_lo and y != yr)

    def band(vals):
        v = np.array([x for y, x in vals.items() if y != yr])
        return float(np.percentile(v, 20)), float(np.percentile(v, 80))

    # ---- normals (1991-2020), smoothed with a 15-day centered window that wraps the year ----
    base = h[h["Y"].between(*NORMALS) & (h["MD"] != "02-29")]
    days = pd.date_range("2001-01-01", "2001-12-31").strftime("%m-%d").tolist()

    def smooth(col):
        s = base.groupby("MD")[col].mean().reindex(days)
        ext = pd.concat([s.iloc[-7:], s, s.iloc[:7]])
        return ext.rolling(15, center=True).mean().iloc[7:-7].round(1)

    n_hi, n_lo = smooth("Hi"), smooth("Lo")
    norm_hi_y = float(n_hi.get(md, np.nan)) if md != "02-29" else None
    norm_lo_y = float(n_lo.get(md, np.nan)) if md != "02-29" else None

    # ---- 2. The year so far, against normals and records set before this year ----
    prior = h[(h["Y"] < yr) & (h["MD"] != "02-29")]
    rec_hi_md = prior.groupby("MD")["Hi"].max().reindex(days)
    rec_lo_md = prior.groupby("MD")["Lo"].min().reindex(days)
    this = h[(h["Y"] == yr) & (h["MD"] != "02-29")].set_index("MD")
    year_days, rec_hi_days, rec_lo_days = [], [], []
    for d in days:
        if d not in this.index:
            continue
        row = this.loc[d]
        hi, lo = row["Hi"], row["Lo"]
        if pd.isna(hi) and pd.isna(lo):
            continue
        set_hi = (not pd.isna(hi)) and hi >= rec_hi_md[d]
        set_lo = (not pd.isna(lo)) and lo <= rec_lo_md[d]
        if set_hi:
            rec_hi_days.append({"md": d, "v": int(hi), "tie": bool(hi == rec_hi_md[d]), "old": int(rec_hi_md[d])})
        if set_lo:
            rec_lo_days.append({"md": d, "v": int(lo), "tie": bool(lo == rec_lo_md[d]), "old": int(rec_lo_md[d])})
        year_days.append([d, None if pd.isna(hi) else int(hi), None if pd.isna(lo) else int(lo),
                          int(set_hi), int(set_lo)])
    above = sum(1 for d, hi, lo, *_ in year_days if hi is not None and lo is not None
                and (hi + lo) / 2 > (n_hi[d] + n_lo[d]) / 2)
    counted = sum(1 for d, hi, lo, *_ in year_days if hi is not None and lo is not None)

    # year-to-date precipitation vs the 1991-2020 average to the same date
    ytd = h[(h["Y"] == yr)]["Pr"]
    ytd_total = round(float(ytd.sum()), 2)
    ytd_missing = int(ytd.isna().sum())
    norm_ytd = []
    for y in range(NORMALS[0], NORMALS[1] + 1):
        s = h[(h["Y"] == y) & (h["MD"] <= md)]["Pr"]
        if len(s) and s.isna().sum() <= 9:
            norm_ytd.append(float(s.sum()))
    norm_ytd = round(float(np.mean(norm_ytd)), 2)

    # ---- 3. The record book ----
    hot = have.sort_values(["Hi", "D"], ascending=[False, True]).head(10)
    cold = have.sort_values(["Lo", "D"], ascending=[True, True]).head(10)
    top_hot = [{"date": d.strftime("%Y-%m-%d"), "v": int(v)} for d, v in zip(hot["D"], hot["Hi"])]
    top_cold = [{"date": d.strftime("%Y-%m-%d"), "v": int(v)} for d, v in zip(cold["D"], cold["Lo"])]

    # standing daily records by the decade that set them (ties split equally),
    # with the count expected if every year had the same chance
    def standing(col, fn):
        """Standing daily records held by each year (ties shared), and the share each year
        would hold if every year had the same chance."""
        got_y, exp_y = {}, {}
        for d, g in h.dropna(subset=[col]).groupby("MD"):
            if d == "02-29":
                continue
            v = fn(g[col])
            yrs = g.loc[g[col] == v, "Y"].tolist()
            for y in yrs:
                got_y[y] = got_y.get(y, 0) + 1 / len(yrs)
            for y in g["Y"]:
                exp_y[y] = exp_y.get(y, 0) + 1 / len(g)
        got = {y // 10 * 10: 0.0 for y in exp_y}
        exp = dict(got)
        for y, v in got_y.items():
            got[y // 10 * 10] += v
        for y, v in exp_y.items():
            exp[y // 10 * 10] += v
        return got, exp, got_y

    def chance_test(got_y, since=2000):
        """How unusual is the count of standing records held by the years since 2000?
        Draw that many years at random from the whole record, 20,000 times, and see how
        often they hold as few (or as many). Whole years are drawn, so a cold snap that set
        several records at once stays together. Years with under 300 days are left out of
        the draw (the current year is in the count, as it is on the page)."""
        days = h.groupby("Y")["Lo"].count()
        pool = np.array([got_y.get(y, 0.0) for y in days.index if days[y] >= 300])
        recent = [y for y in days.index if y >= since]
        obs = sum(got_y.get(y, 0.0) for y in recent)
        rng = np.random.default_rng(2026)
        draws = np.array([pool[rng.choice(len(pool), len(recent), replace=False)].sum() for _ in range(20000)])
        p_fewer, p_more = float((draws <= obs).mean()), float((draws >= obs).mean())
        side, p = ("fewer", p_fewer) if p_fewer < p_more else ("more", p_more)
        cls = "signal" if p < 0.025 else "edge" if p < 0.06 else "noise"
        return {"side": side, "p": round(p, 4), "class": cls, "years": len(recent),
                "typical": [round(float(np.percentile(draws, 2.5))), round(float(np.percentile(draws, 97.5)))]}

    sh, eh, sh_y = standing("Hi", max)
    sl, el, sl_y = standing("Lo", min)
    test_lo, test_hi = chance_test(sl_y), chance_test(sh_y)
    decades = sorted(set(eh) | set(el))
    rec_dec = [{"d": d, "yrs": int(h[h["Y"] // 10 * 10 == d]["Y"].nunique()),
                "hi": round(sh.get(d, 0), 1), "lo": round(sl.get(d, 0), 1),
                "ehi": round(eh.get(d, 0), 1), "elo": round(el.get(d, 0), 1)} for d in decades]
    since2000 = [r for r in rec_dec if r["d"] >= 2000]
    lows_since_2000 = round(sum(r["lo"] for r in since2000))
    lows_expected_since_2000 = round(sum(r["elo"] for r in since2000))
    highs_since_2000 = round(sum(r["hi"] for r in since2000))
    highs_expected_since_2000 = round(sum(r["ehi"] for r in since2000))

    # ---- 4. How it has changed ----
    def monthly(col):
        g = h.groupby(["Y", "M"])[col].agg(["mean", "count"]).reset_index()
        g["dim"] = [calendar.monthrange(y, m)[1] for y, m in zip(g["Y"], g["M"])]
        g["ok"] = (g["dim"] - g["count"] <= MAX_MISSING_PER_MONTH) & np.array([month_done(y, m) for y, m in zip(g["Y"], g["M"])])
        return g

    mh, ml = monthly("Hi"), monthly("Lo")

    def annual(g):
        ok = g[g["ok"]].groupby("Y")["mean"].agg(["mean", "count"])
        return ok[ok["count"] == 12]["mean"]

    ann_hi, ann_lo = annual(mh), annual(ml)
    # use the same years for highs and lows, so the two trends compare like with like
    yrs_complete = sorted(set(ann_hi.index) & set(ann_lo.index))
    ann_hi, ann_lo = ann_hi.loc[yrs_complete], ann_lo.loc[yrs_complete]
    t_hi, t_lo = trend(ann_hi), trend(ann_lo)
    missing_years = [y for y in range(first_year + 1, yr) if y not in yrs_complete]

    month_trends = []
    for m in range(1, 13):
        th = trend(mh[(mh["M"] == m) & mh["ok"]].set_index("Y")["mean"])
        tl = trend(ml[(ml["M"] == m) & ml["ok"]].set_index("Y")["mean"])
        month_trends.append({"m": m, "hi": {k: th[k] for k in ("per_decade", "lo", "hi", "class", "n", "total")},
                             "lo": {k: tl[k] for k in ("per_decade", "lo", "hi", "class", "n", "total")}})

    # month x decade: average temperature vs the 1991-2020 normal for that month
    mt = mh[["Y", "M", "mean", "ok"]].merge(ml[["Y", "M", "mean", "ok"]], on=["Y", "M"], suffixes=("_h", "_l"))
    mt = mt[mt["ok_h"] & mt["ok_l"]].copy()
    mt["T"] = (mt["mean_h"] + mt["mean_l"]) / 2
    normal_m = mt[mt["Y"].between(*NORMALS)].groupby("M")["T"].mean()
    mt["A"] = mt["T"] - mt["M"].map(normal_m)
    mt["Dec"] = mt["Y"] // 10 * 10
    heat = mt.groupby(["Dec", "M"])["A"].agg(["mean", "count"]).reset_index()
    heatmap = [{"d": int(d), "m": int(m), "a": round(float(a), 1), "n": int(n)}
               for d, m, a, n in zip(heat["Dec"], heat["M"], heat["mean"], heat["count"])]

    # ---- 5. Rain and snow ----
    pm = h.groupby(["Y", "M"])["Pr"].agg(["sum", "count"]).reset_index()
    pm["dim"] = [calendar.monthrange(y, m)[1] for y, m in zip(pm["Y"], pm["M"])]
    pm["ok"] = (pm["dim"] - pm["count"] <= MAX_MISSING_PER_MONTH) & np.array([month_done(y, m) for y, m in zip(pm["Y"], pm["M"])])
    pok = pm[pm["ok"]].groupby("Y")["sum"].agg(["sum", "count"])
    ann_pr = pok[pok["count"] == 12]["sum"].round(2)
    t_pr = trend(ann_pr)
    pr_normal = round(float(ann_pr.loc[NORMALS[0]:NORMALS[1]].mean()), 2)
    wettest = ann_pr.sort_values(ascending=False).head(3)
    driest = ann_pr.sort_values().head(3)

    # dry spells: days in a row with no measurable rain (trace counts as dry); a missing day ends a spell
    spells, run_start, run_len = [], None, 0
    for d, p in zip(h["D"], h["Pr"]):
        if pd.isna(p) or p > 0:
            if run_len >= 14:
                spells.append((run_start, run_len))
            run_start, run_len = None, 0
            continue
        if run_len == 0:
            run_start = d
        run_len += 1
    if run_len >= 14:
        spells.append((run_start, run_len))
    # The chart shows "long for this place": the shortest length (14, 30, 60 or 90 days) that
    # leaves no more than about 1.5 spells a year. 14 days is long in Goshen; ordinary in Phoenix.
    n_years = h["Y"].nunique()
    dry_min = next((t for t in (14, 30, 60, 90) if sum(1 for _, n in spells if n >= t) <= 1.5 * n_years), 120)
    spells = [(st, n) for st, n in spells if n >= dry_min]
    dry = [{"y": int(s.year), "doy": int(s.dayofyear), "n": int(n), "start": s.strftime("%Y-%m-%d")} for s, n in spells]
    longest = max(spells, key=lambda t: t[1])
    pr_years = sorted(int(y) for y in h.dropna(subset=["Pr"])["Y"].unique())

    # seasonal snowfall (July-June); a season counts if Nov-Mar each have no more than 9 missing days
    h["Season"] = np.where(h["M"] >= 7, h["Y"], h["Y"] - 1)
    snow = []
    for s, g in h.groupby("Season"):
        if s >= yr - (0 if asof.month >= 7 else 1) or s < first_year:
            continue
        good = True
        for m in (11, 12, 1, 2, 3):
            y = s if m >= 7 else s + 1
            gm = g[(g["M"] == m)]
            dim = calendar.monthrange(y, m)[1]
            if dim - gm["Sn"].count() > MAX_MISSING_PER_MONTH:
                good = False
        if good:
            snow.append({"s": int(s), "v": round(float(g["Sn"].sum()), 1)})
    snow_s = pd.Series({r["s"]: r["v"] for r in snow})
    t_sn = trend(snow_s)
    sn_normal = round(float(snow_s.loc[NORMALS[0]:NORMALS[1] - 1].mean()), 1)

    # ---- the words: every title and paragraph is chosen from this city's numbers ----
    C = city["name"]
    Cs = C + ("'" if C.endswith("s") else "'s")   # possessive: Los Olivos', Chicago's
    n_rec_hi, n_rec_lo = len(rec_hi_days), len(rec_lo_days)
    pct_above = round(100 * above / counted)

    def plural(n, word):
        return f"{n} {word}{'' if n == 1 else 's'}"

    def sgn(v, nd=2):
        return ("+" if v > 0 else "−" if v < 0 else "") + f"{abs(v):.{nd}f}"

    def direction(t):
        """'up', 'down' or None (no clear change). Only a signal counts."""
        if t["class"] != "signal":
            return None
        return "up" if t["per_decade"] > 0 else "down"

    def period_check(series, t):
        """methods.md: a change claim must hold two ways. The first and last 30 complete
        years must differ in the same direction as the trend."""
        s = series.dropna()
        if len(s) < 60:
            return False
        early, late = s.iloc[:30].mean(), s.iloc[-30:].mean()
        return (late - early > 0) == (t["per_decade"] > 0)

    # 1. yesterday
    hi_words = rank_words(r_hi, asof, "high")
    lo_words = rank_words(r_lo, asof, "low")
    D_ = md_words(asof)
    since_hi = (f", the {'warmest' if r_hi['standing'] == 'warm' else 'coolest'} since {r_hi['since']}"
                if r_hi["notable"] and r_hi["since"] and not r_hi["record"] else "")
    if r_hi["record"]:
        lede = f"Yesterday's high of {y_hi}°F {hi_words}."
    elif r_hi["notable"]:
        lede = f"Yesterday's high of {y_hi}°F was the {hi_words}{since_hi}."
    else:
        lede = f"Yesterday's high of {y_hi}°F was {hi_words}."
    if r_hi["notable"] and r_hi["standing"] == "warm":
        t_yest = f"{D_}: warmer than {r_hi['percentile']}% of years"
    elif r_hi["notable"]:
        t_yest = f"{D_}: cooler than {100 - r_hi['percentile']}% of years"
    elif r_lo["notable"]:
        t_yest = f"{D_}: a typical high, but a {'warm' if r_lo['standing'] == 'warm' else 'cold'} night"
    else:
        t_yest = f"{D_} was a typical day"
    hi_sentence = (f"The high of <strong>{y_hi}°F</strong> {'' if r_hi['record'] else 'was '}"
                   f"{'the ' if r_hi['notable'] and not r_hi['record'] else ''}{hi_words}{since_hi}.")
    lo_sentence = (f"The low of <strong>{y_lo}°F</strong> {'' if r_lo['record'] else 'was '}"
                   f"{'the ' if r_lo['notable'] and not r_lo['record'] else ''}{lo_words}.")
    rec_hi_txt = ", ".join(map(str, rec_hi_years))
    p_yest = (f"{hi_sentence} The normal high for the date is {round(norm_hi_y)}°F, and the record is "
              f"{rec_hi}°F ({rec_hi_txt}). {lo_sentence}")

    # 3. record book
    ratio_lo = lows_since_2000 / lows_expected_since_2000
    ratio_hi = highs_since_2000 / highs_expected_since_2000

    def vs_chance(n, e):
        r = n / e
        cmp = ("far fewer than" if r <= 0.5 else "fewer than" if r < 0.75 else "close to" if r <= 1.25
               else "more than" if r < 1.5 else "far more than")
        return f"{cmp} the {e} that chance alone would give"

    def count_words(n, what):
        return f"no {what}s at all" if n == 0 else f"{n} {what}{'' if n == 1 else 's'}"

    # "far fewer / far more" needs both: a big gap (half or double) and a real one (the test)
    lows_far = ratio_lo <= 0.5 and test_lo["class"] == "signal" and test_lo["side"] == "fewer"
    highs_far = ratio_hi >= 1.5 and test_hi["class"] == "signal" and test_hi["side"] == "more"
    SIG = ' class="sig"'
    if lows_far and highs_far:
        t_rec = "Record highs keep coming. Record lows have nearly stopped."
    elif lows_far:
        t_rec = f"{C} has nearly stopped setting record lows"
    elif highs_far:
        t_rec = f"{C} is setting record highs far faster than chance"
    else:
        t_rec = "Record highs and lows, by the decade that set them"
    top_dec = max(rec_dec, key=lambda r: r["hi"])
    hot0 = top_hot[0]
    hot0_d = date.fromisoformat(hot0["date"])
    p_rec = (f"If {Cs} climate weren't changing, every year would have the same chance of holding a daily record. "
             f"Since 2000, {C} has set <strong{SIG if lows_far else ''}>{count_words(lows_since_2000, 'standing record low')}"
             f"</strong>, {vs_chance(lows_since_2000, lows_expected_since_2000)}, and "
             f"<strong{SIG if highs_far else ''}>{count_words(highs_since_2000, 'record high')}</strong>, "
             f"{vs_chance(highs_since_2000, highs_expected_since_2000)}. "
             f"The {top_dec['d']}s hold the most record highs of any decade ({round(top_dec['hi'])}). "
             f"{Cs} all-time high is {hot0['v']}°F, on {hot0_d:%B} {hot0_d.day}, {hot0_d.year}.")
    sig_bars = {"lo": lows_far, "hi": highs_far}

    # 4. how it has changed
    d_hi, d_lo = direction(t_hi), direction(t_lo)
    # A long record can join several sites (downtown, then an airport). A move can hide or fake a
    # long-run trend, so compare with the last 40 complete years. If they disagree, say so plainly
    # instead of making a flat claim.
    RECENT = 40
    t_hi40, t_lo40 = trend(ann_hi.iloc[-RECENT:]), trend(ann_lo.iloc[-RECENT:])
    d_hi40, d_lo40 = direction(t_hi40), direction(t_lo40)
    conflict = [w for w, d, d40 in (("low", d_lo, d_lo40), ("high", d_hi, d_hi40))
                if d40 is not None and d40 != d]
    since40 = t_lo40["first"]

    def period_phrase(d, d40):
        """How one measure changed, naming the period when the full record and the last 40
        years disagree. Only signals count as change."""
        return {
            ("up", "up"): "have warmed", ("down", "down"): "have cooled",
            ("up", None): "warmed over the long run", ("down", None): "cooled over the long run",
            (None, "up"): f"have warmed since {since40}", (None, "down"): f"have cooled since {since40}",
            ("up", "down"): f"warmed over the long run but have cooled since {since40}",
            ("down", "up"): f"cooled over the long run but have warmed since {since40}",
            (None, None): "show no clear change",
        }[(d, d40)]

    if conflict:
        lo_p, hi_p = period_phrase(d_lo, d_lo40), period_phrase(d_hi, d_hi40)
        if lo_p == hi_p:
            t_chg = f"{Cs} days and nights {lo_p}"
        else:
            t_chg = f"{Cs} nights {lo_p}. Its days {hi_p}."
    elif d_lo == "up" and d_hi is None:
        t_chg = f"{Cs} nights got warmer. Its days didn't."
    elif d_hi == "up" and d_lo is None:
        t_chg = f"{Cs} days got warmer. Its nights didn't."
    elif d_hi == "up" and d_lo == "up":
        if t_lo["lo"] > t_hi["hi"]:
            t_chg = f"{Cs} days and nights both warmed. Nights warmed faster."
        elif t_hi["lo"] > t_lo["hi"]:
            t_chg = f"{Cs} days and nights both warmed. Days warmed faster."
        else:
            t_chg = f"{C} has warmed, days and nights alike"
    elif d_lo == "up" and d_hi == "down":
        t_chg = f"{Cs} nights got warmer while its days cooled"
    elif d_hi is None and d_lo is None:
        t_chg = f"No clear change in {Cs} highs or lows"
    else:
        t_chg = f"{Cs} nights {period_phrase(d_lo, d_lo40)}. Its days {period_phrase(d_hi, d_hi40)}."

    def trend_sentence(t, what):
        d = direction(t)
        if d:
            verb = "risen" if d == "up" else "fallen"
            return (f"{Cs} average {what} has {verb} <strong class='sig'>{sgn(t['per_decade'])}°F per decade</strong> "
                    f"(95% range {sgn(t['lo'])} to {sgn(t['hi'])}), about {sgn(t['total'], 1)}°F since {t['first']}.")
        near = " It's close, but the range still includes zero." if t["class"] == "edge" else ""
        return (f"{Cs} average {what} shows no clear change: {sgn(t['per_decade'])}°F per decade, "
                f"a range ({sgn(t['lo'])} to {sgn(t['hi'])}) that includes zero.{near}")

    # summer highs that fell: say what the record shows, not why
    jul_aug = [month_trends[i]["hi"] for i in (6, 7)]
    summer_note = ""
    if all(t["class"] == "signal" and t["per_decade"] < 0 for t in jul_aug):
        ja = mh[mh["M"].isin([7, 8]) & mh["ok"]].copy()
        ja["Dec"] = ja["Y"] // 10 * 10
        hottest = int(ja.groupby("Dec")["mean"].mean().idxmax())
        summer_note = (f" July and August highs have edged down across the full record; "
                       f"the {hottest}s summers were the hottest in it.")
    p_chg = (f"Across {len(yrs_complete)} complete years: " + trend_sentence(t_lo, "low") + " "
             + trend_sentence(t_hi, "high") + summer_note)
    if conflict:
        first40 = t_lo40["first"]
        parts = []
        for w, t40 in (("lows", t_lo40), ("highs", t_hi40)):
            d40 = direction(t40)
            if d40:
                parts.append(f"{w} have {'risen' if d40 == 'up' else 'fallen'} <strong class='sig'>{sgn(t40['per_decade'])}°F per decade</strong> "
                             f"(95% range {sgn(t40['lo'])} to {sgn(t40['hi'])})")
            else:
                parts.append(f"{w} show no clear change")
        p_chg += (f" <strong>The last {RECENT} years tell a different story:</strong> since {first40}, " + " and ".join(parts) + ". "
                  + ("The full record joins different observing sites over the years, and a move (for example, from the "
                     "city to a cooler airport) can hide or exaggerate a long-run trend, so the recent decades are the "
                     "clearer guide here." if city["kind"] == "threadex" else
                     "At a volunteer station, a change in the exact site, the instruments or the time of day it's read "
                     "can hide or exaggerate a long-run trend, so read both periods with care."))

    def months_phrase(field, word):
        up = [MONTHS[m["m"] - 1] for m in month_trends if m[field]["class"] == "signal" and m[field]["per_decade"] > 0]
        down = [MONTHS[m["m"] - 1] for m in month_trends if m[field]["class"] == "signal" and m[field]["per_decade"] < 0]
        out = f"{word} rose in {'none' if not up else ('all 12 months' if len(up) == 12 else f'{len(up)} of 12 months')}"
        if down:
            out += f"; {' and '.join(down) if len(down) <= 2 else f'{len(down)} months'} fell"
        return out

    t_mon = months_phrase("lo", "Lows") + ". " + months_phrase("hi", "Highs") + "."

    # 5. rain and snow
    d_pr = direction(t_pr)
    show_snow = sn_normal >= 1.0 and len(snow) >= 30
    t_rain = {"up": f"{C} has gotten wetter", "down": f"{C} has gotten drier"}.get(
        d_pr, f"{'Rain and snow' if show_snow else 'Rain'}: no clear long-term change")
    if d_pr:
        rain_trend = (f"{Cs} yearly precipitation has {'risen' if d_pr == 'up' else 'fallen'} "
                      f"<strong class='sig'>{sgn(t_pr['per_decade'])} inches per decade</strong> "
                      f"(95% range {sgn(t_pr['lo'])} to {sgn(t_pr['hi'])}), about {sgn(t_pr['total'], 1)} inches since {t_pr['first']}.")
    else:
        rain_trend = (f"{Cs} yearly precipitation shows no clear long-term change "
                      f"({sgn(t_pr['per_decade'])} inches per decade, a range that includes zero).")
    w0, d0 = wettest.index[0], driest.index[0]
    p_rain = (f"{rain_trend} The 1991–2020 normal is {pr_normal:.1f} inches a year. "
              f"The wettest year was {w0} ({wettest.iloc[0]:.2f} in); the driest, {d0} ({driest.iloc[0]:.2f} in).")
    if not show_snow:
        p_rain += (f" {C} almost never sees snow (the 1991–2020 average is {sn_normal:.1f} inches a winter), "
                   f"so there's no snowfall chart.")

    text = {"lede": lede, "t_yest": t_yest, "p_yest": p_yest, "t_rec": t_rec, "p_rec": p_rec,
            "t_chg": t_chg, "p_chg": p_chg, "t_mon": t_mon, "t_rain": t_rain, "p_rain": p_rain,
            "show_snow": show_snow, "sig_bars": sig_bars,
            "t_year": (f"{yr} so far: {plural(n_rec_hi, 'daily record high')}, "
                       f"{'no' if n_rec_lo == 0 else n_rec_lo} daily record low{'' if n_rec_lo == 1 else 's'}")}

    # ---- checks: stop instead of publishing something untrue ----
    claims = {
        "Rank = years strictly warmer + 1 (recounted)":
            r_hi["warm_rank"] == sum(1 for y, v in hi_vals.items() if y != yr and v > y_hi) + 1,
        "Percentile and rank agree on the side of normal":
            not r_hi["notable"] or ((r_hi["standing"] == "warm") == (r_hi["percentile"] >= 50)),
        "Normals period has at least 20 years of data": base["Y"].nunique() >= 20,
        "Same-date sample is at least 30 years (methods.md)": r_hi["years"] >= 30 and r_lo["years"] >= 30,
        "At least 30 complete years for trends": len(yrs_complete) >= 30,
        "Lows change claim holds two ways (trend and first vs last 30 years)":
            d_lo is None or period_check(ann_lo, t_lo),
        "Highs change claim holds two ways": d_hi is None or period_check(ann_hi, t_hi),
        "Precipitation change claim holds two ways": d_pr is None or period_check(ann_pr, t_pr),
        "All-time high in the record book is the record's maximum": hot0["v"] == int(have["Hi"].max()),
        "No 'didn't warm' headline when the last 40 years disagree": not (conflict and "didn't" in t_chg),
        "No placeholder headline": "How " not in t_chg[:4],
        "'Far fewer record lows' only when the resampling test agrees":
            not lows_far or (test_lo["class"] == "signal" and test_lo["side"] == "fewer"),
        "'Far more record highs' only when the resampling test agrees":
            not highs_far or (test_hi["class"] == "signal" and test_hi["side"] == "more"),
    }
    broken = [k for k, ok in claims.items() if not ok]
    if broken:
        raise ClaimError("; ".join(broken))
    titles = {"year": text["t_year"], "records": t_rec, "change": t_chg, "rain": t_rain}

    data = {
        "slug": slug, "city": city, "asof": asof.isoformat(), "year": yr, "first_year": first_year,
        "normals": list(NORMALS),
        "yesterday": {
            "md": md, "words": md_words(asof), "hi": y_hi, "lo": y_lo, "r_hi": r_hi, "r_lo": r_lo,
            "hi_words": hi_words, "lo_words": lo_words, "lede": lede,
            "norm_hi": norm_hi_y, "norm_lo": norm_lo_y,
            "rec_hi": rec_hi, "rec_hi_years": rec_hi_years, "rec_lo": rec_lo, "rec_lo_years": rec_lo_years,
            "band_hi": band(hi_vals), "band_lo": band(lo_vals),
            "hi_vals": sorted(hi_vals.items()), "lo_vals": sorted(lo_vals.items()),
        },
        "season": {
            "days": days, "norm_hi": n_hi.tolist(), "norm_lo": n_lo.tolist(),
            "rec_hi": [None if pd.isna(v) else int(v) for v in rec_hi_md], "rec_lo": [None if pd.isna(v) else int(v) for v in rec_lo_md],
            "year": year_days, "rec_hi_days": rec_hi_days, "rec_lo_days": rec_lo_days,
            "pct_above": pct_above, "counted": counted,
            "ytd_pr": ytd_total, "ytd_missing": ytd_missing, "norm_ytd_pr": norm_ytd,
        },
        "records": {"hot": top_hot, "cold": top_cold, "decades": rec_dec, "test_lo": test_lo, "test_hi": test_hi,
                    "lows_since_2000": lows_since_2000, "lows_expected_since_2000": lows_expected_since_2000,
                    "highs_since_2000": highs_since_2000, "highs_expected_since_2000": highs_expected_since_2000},
        "change": {
            "ann_hi": [[int(y), round(float(v), 1)] for y, v in ann_hi.items()],
            "ann_lo": [[int(y), round(float(v), 1)] for y, v in ann_lo.items()],
            "t_hi": t_hi, "t_lo": t_lo, "t_hi40": t_hi40, "t_lo40": t_lo40, "conflict": conflict, "months": month_trends, "heat": heatmap,
            "complete_years": len(yrs_complete), "missing_years": missing_years,
        },
        "rain": {
            "ann": [[int(y), float(v)] for y, v in ann_pr.items()], "t": t_pr, "normal": pr_normal,
            "wettest": [[int(y), float(v)] for y, v in wettest.items()],
            "driest": [[int(y), float(v)] for y, v in driest.items()],
            "dry": dry, "dry_min": dry_min, "dry_years": pr_years,
            "longest": {"start": longest[0].strftime("%Y-%m-%d"), "n": int(longest[1])},
            "snow": snow, "t_snow": t_sn, "snow_normal": sn_normal,
        },
        "titles": titles,
        "text": text,
    }
    return data


def switch_html(slug):
    """The record switch for a volunteer-station town and its official city (both directions).
    Each choice is a separate page built entirely from one station."""
    pair = CITIES[slug].get("pair")
    partner = next((k for k, c in CITIES.items() if c.get("pair", {}).get("slug") == slug), None)
    if pair:
        local, official, miles = slug, pair["slug"], pair["miles"]
    elif partner:
        local, official, miles = partner, slug, CITIES[partner]["pair"]["miles"]
    else:
        return ""
    if not (HISTORY_DIR / f"{official}.csv").exists() or not (HISTORY_DIR / f"{local}.csv").exists():
        return ""
    opts = [(local, f"{CITIES[local]['name']} volunteer station", "the town's own record"),
            (official, f"{CITIES[official]['name']} official record", f"about {miles} miles away")]
    links = "".join(
        f'<a href="../{s_}/"{" aria-current=\"page\"" if s_ == slug else ""}><b>{label}</b><span>{sub}</span></a>'
        for s_, label, sub in opts)
    return f'<nav class="switch" aria-label="Choose the record">{links}</nav>'


def render(data):
    tpl = (HERE / "template.html").read_text(encoding="utf-8")
    c, y = data["city"], data["yesterday"]
    asof = date.fromisoformat(data["asof"])
    fill = {
        "CITY": c["name"], "STATE": c["state"], "ST": c["st"], "FIRST_YEAR": str(data["first_year"]),
        "ASOF_WORDS": f"{asof:%B} {asof.day}, {asof.year}", "LEDE": y["lede"],
        "RECORD_NAME": c["record_name"], "RECORD_KIND": c["record_kind"],
        "SLUG": data["slug"], "SWITCH": switch_html(data["slug"]), "MAIN": MAIN_SITE,
        "DATA": json.dumps(data, separators=(",", ":")),
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    left = [p for p in tpl.split("{{")[1:] if "}}" in p[:40]]
    if left:
        raise RuntimeError(f"Unfilled placeholders: {[p.split('}}')[0] for p in left]}")
    return tpl


def write_city(slug, out_dir, history=None):
    data = build(slug, history)
    out = out_dir / "almanac" / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "data.json").write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    (out / "index.html").write_text(render(data), encoding="utf-8")
    return data


def index_row(d):
    """One place's line on the front page (map dot + table row)."""
    c, r, g, t = d["city"], d["records"], d["change"], d["text"]
    x, y = project(c["lon"], c["lat"])
    trend = lambda tr: {"v": tr["per_decade"], "c": tr["class"]}  # noqa: E731
    return {"slug": d["slug"], "name": c["name"], "st": c["st"], "state": c["state"],
            "kind": c["kind"], "since": d["first_year"], "x": round(x, 1), "y": round(y, 1),
            "lows": r["lows_since_2000"], "lows_exp": r["lows_expected_since_2000"],
            "test": r["test_lo"]["class"], "side": r["test_lo"]["side"], "far": t["sig_bars"]["lo"],
            "nights": trend(g["t_lo"]), "days": trend(g["t_hi"]), "mixed": bool(g["conflict"]),
            "headline": t["t_chg"], "yhi": d["yesterday"]["hi"], "yhi_words": d["yesterday"]["hi_words"]}


def write_index(built, out_dir):
    """The Almanac's front page: search, a US map (one dot per place) and a sortable table."""
    rows = sorted((index_row(d) for d in built), key=lambda r: r["name"])
    n, k = len(rows), sum(1 for r in rows if r["far"])
    places = "place" if n == 1 else "places"
    if k == n:
        lede = f"Every one of these {n} {places} has nearly stopped setting record lows."
    elif k:
        lede = f"{k} of these {n} {places} have nearly stopped setting record lows."
    else:
        lede = f"None of these {n} {places} has clearly stopped setting record lows."
    # check: the lede's count is the number of orange dots
    if k != sum(1 for r in rows if r["test"] == "signal" and r["side"] == "fewer" and r["far"]):
        raise ClaimError("front-page lede count does not match the map")
    states = json.loads((HERE / "us_states.json").read_text(encoding="utf-8"))
    paths = "".join(f'<path d="{d}"><title>{html.escape(name)}</title></path>' for name, d in states["states"].items())
    asof = max(date.fromisoformat(d["asof"]) for d in built)
    tpl = (HERE / "index_template.html").read_text(encoding="utf-8")
    page = (tpl.replace("{{STATES}}", paths).replace("{{DATA}}", json.dumps(rows, separators=(",", ":")))
               .replace("{{COUNT}}", str(n)).replace("{{LEDE}}", lede)
               .replace("{{ASOF_WORDS}}", f"{asof:%B} {asof.day}, {asof.year}").replace("{{MAIN}}", MAIN_SITE))
    left = [p for p in page.split("{{")[1:] if "}}" in p[:40]]
    if left:
        raise RuntimeError(f"Unfilled placeholders on the index: {[p.split('}}')[0] for p in left]}")
    (out_dir / "almanac").mkdir(parents=True, exist_ok=True)
    (out_dir / "almanac" / "index.html").write_text(page, encoding="utf-8")
    # The site's front page points to the Almanac until the live page takes its place.
    (out_dir / "index.html").write_text(
        '<!doctype html><meta charset="utf-8"><title>Weather · Tall Order Digital</title>'
        '<meta name="robots" content="noindex"><meta http-equiv="refresh" content="0; url=almanac/">'
        '<p><a href="almanac/">Almanac</a></p>', encoding="utf-8")


def summary(data):
    y, ch, r = data["yesterday"], data["change"], data["rain"]
    return (f"high {y['hi']}°F ({y['hi_words']}); lows {ch['t_lo']['per_decade']:+.2f}°F/decade ({ch['t_lo']['class']}), "
            f"highs {ch['t_hi']['per_decade']:+.2f} ({ch['t_hi']['class']}); \"{data['text']['t_chg']}\"")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("city", nargs="?", choices=sorted(CITIES))
    ap.add_argument("--all", action="store_true", help="every city that has a history file, plus the index")
    ap.add_argument("--history", help="a different history CSV for a single city (testing)")
    ap.add_argument("--out", default=str(SITE_DIR), help="output folder (default: site/)")
    a = ap.parse_args()
    out_dir = Path(a.out)
    if not a.all and not a.city:
        ap.error("name a city or use --all")
    slugs = [s for s in CITIES if (HISTORY_DIR / f"{s}.csv").exists()] if a.all else [a.city]
    built, failed = [], []
    for slug in slugs:
        try:
            data = write_city(slug, out_dir, None if a.all else a.history)
            built.append(data)
            print(f"{CITIES[slug]['name']:<14} OK   data through {data['asof']}: {summary(data)}")
        except QualityError as e:
            failed.append(slug)
            print(f"{CITIES[slug]['name']:<14} REVIEW the record looks wrong, page not written: {e}")
        except ClaimError as e:
            failed.append(slug)
            print(f"{CITIES[slug]['name']:<14} SKIP a claim isn't true for this city, page not written: {e}")
        except Exception as e:
            failed.append(slug)
            print(f"{CITIES[slug]['name']:<14} FAIL {type(e).__name__}: {e}")
    if a.all and built:
        write_index(built, out_dir)
    print(f"{len(built)} of {len(slugs)} Almanac pages written to {out_dir}.")
    return 1 if not built else 0


if __name__ == "__main__":
    sys.exit(main())
