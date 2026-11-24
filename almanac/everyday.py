"""
"Every day" pages: one per place, at weather/almanac/<place>/every-day/ (Nov 16, 2026).

The showpiece "How warm was every day since 1900 in your city?": one row per year since 1900 (or the record's first full year),
one square per day, each colored by how far that day's high was from the 1991-2020 normal high for the date. The
newest day fills in each morning, because build.py rebuilds every page in the daily run.

Built by build.py right after each place's overview and records page, from the same official record and the same
smoothed 1991-2020 normals the overview uses (data["season"]["norm_hi"]), so the pages agree. Every sentence is
chosen from the place's own numbers and checked (recounted from the raw rows, and from the page's own encoded
data, which also proves the encoding round-trips). If a check fails, the page isn't written that day and
carry_over.py keeps the last good one.

The same template also builds the showpiece's front page on the main site (tallorderdigital.com/weather/
every-day-since-1900/), featuring one city:

    python almanac/everyday.py --showpiece chicago OUT_DIR

That page is copied into tallorderdigital-site by hand (like any story page), carries a snapshot of the featured
city's data, and loads the city's live days.json from the Almanac each visit so its newest square stays current.
"""

import argparse
import html
import json
import math
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
START_YEAR = 1900            # rows start here, or at the record's first full year if later
MIN_YEAR_DAYS = 300          # a year with fewer days with a high doesn't start the chart
FULL_YEAR_DAYS = 330         # a "complete" year for the warmest/coolest-year sentence
FULL_DECADE_DAYS = 2900      # a decade compared in the lede has at least this many days (about 80%)
MIN_WINDOW_SHARE = 0.9       # a year counts in the year-so-far rank if it has 90% of the window's days
TITLE_MAX = 62               # seo-standards.md, " · Tall Order Digital" included
SUFFIX = " · Tall Order Digital"
HUB_PATH = "weather/every-day-since-1900/"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
# Each day's high is stored as one character: its difference from the date's rounded normal high,
# offset into this alphabet (90 printable characters; no quote, backslash or "<", so it is safe inside JSON in HTML).
ALPHA = "".join(chr(c) for c in range(35, 127) if chr(c) not in "\\<")
D_MIN = -44                  # ALPHA[0] stands for 44 °F below the rounded normal
D_MAX = D_MIN + len(ALPHA) - 1
MISSING, EXCEPTION = " ", "!"  # no reading (or no such date) / beyond the alphabet: value in "exc"


class EverydayError(Exception):
    """A sentence the page would print is not true for this place's data."""


# ------------------------------------------------------------------ small helpers
def whole(v):
    return int(math.floor(float(v) + 0.5))


def idx_of(md):
    """Day index in a leap year: Jan 1 = 0, Feb 29 = 59, Dec 31 = 365."""
    return (date(2000, int(md[:2]), int(md[3:])) - date(2000, 1, 1)).days


def md_of(i):
    d = date(2000, 1, 1) + timedelta(days=i)
    return f"{d:%m-%d}"


def long_date(d):
    return f"{d:%B} {d.day}, {d.year}"


def short_date(d):
    return f"{d:%b}. {d.day}, {d.year}".replace("May.", "May")


def f_deg(v, plus=False):
    s = f"{abs(v):.1f}" if isinstance(v, float) else str(abs(int(v)))
    if v < 0:
        return "−" + s
    return ("+" if plus and v > 0 else "") + s


def possessive(name):
    return name + ("’" if name.endswith("s") else "’s")


def ordinal(n):
    n = int(n)
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def rank_words(k):
    return "highest" if k == 1 else f"{ordinal(k)}-highest"


# ------------------------------------------------------------------ the numbers
def normals366(ov):
    """The overview's smoothed 1991-2020 normal high for every date, with Feb 29 between Feb 28 and Mar 1."""
    s = ov["season"]
    by_md = dict(zip(s["days"], s["norm_hi"]))
    out = []
    for i in range(366):
        md = md_of(i)
        v = by_md.get(md) if md != "02-29" else (by_md["02-28"] + by_md["03-01"]) / 2
        if v is None or (isinstance(v, float) and math.isnan(v)):
            raise EverydayError(f"no normal high for {md}")
        out.append(round(float(v), 1))
    return out


def encode(h, y0, yr, norm):
    """One string per year (366 characters) and the out-of-range values. Returns (rows, exc)."""
    have = h.dropna(subset=["Hi"])
    have = have[have["Y"].between(y0, yr)]
    grid = {y: [MISSING] * 366 for y in range(y0, yr + 1)}
    exc = {}
    for y, md, v in zip(have["Y"], have["MD"], have["Hi"]):
        i = idx_of(md)
        d = int(v) - whole(norm[i])
        if D_MIN <= d <= D_MAX:
            grid[int(y)][i] = ALPHA[d - D_MIN]
        else:
            grid[int(y)][i] = EXCEPTION
            exc[f"{int(y)}-{i}"] = int(v)
    return ["".join(grid[y]) for y in range(y0, yr + 1)], exc


def decode(rows, exc, y0, norm):
    """The page script's reading of the encoded data, in Python: {(year, day index): high}."""
    out = {}
    for k, s in enumerate(rows):
        y = y0 + k
        for i, ch in enumerate(s):
            if ch == MISSING:
                continue
            out[(y, i)] = exc[f"{y}-{i}"] if ch == EXCEPTION else whole(norm[i]) + ALPHA.index(ch) + D_MIN
    return out


def build_everyday(ov, h, cities, today=None):
    """Everything the page says, checked. ov: build.build()'s data; h: build.load()'s frame."""
    today = today or date.today()
    slug, city = ov["slug"], cities[ov["slug"]]
    asof = date.fromisoformat(ov["asof"])
    yr = asof.year
    norm = normals366(ov)

    have = h.dropna(subset=["Hi"])
    per_year = have.groupby("Y").size()
    full = [int(y) for y, n in per_year.items() if n >= MIN_YEAR_DAYS]
    if not full:
        raise EverydayError("no year with enough days")
    y0 = max(START_YEAR, min(full))
    x = have[have["Y"].between(y0, yr)].copy()
    x["i"] = x["MD"].map(idx_of)
    x["norm"] = x["i"].map(lambda i: norm[i])
    x["a"] = x["Hi"] - x["norm"]
    x["up"] = x["a"] > 0
    if x.empty or asof not in set(x["D"].dt.date):
        raise EverydayError("the newest day has no high")
    C = city["name"]

    # ---- 1. The whole chart and the lede ----
    n_all, up_all = int(len(x)), int(x["up"].sum())
    p_all = round(100 * up_all / n_all)
    x["dec"] = (x["Y"] // 10) * 10
    decades = []
    for dec, g in x.groupby("dec"):
        first, last = max(int(dec), y0), min(int(dec) + 9, yr)
        label = f"{dec}s" if first == dec and last == dec + 9 else (
            f"{dec}s so far" if first == dec else f"{first}–{last}")
        decades.append({"dec": int(dec), "label": label, "first": first, "last": last, "n": int(len(g)),
                        "up": int(g["up"].sum()), "p": round(100 * g["up"].mean()),
                        "a": round(float(g["a"].mean()), 1), "partial": last < dec + 9 or first > dec})
    early = next((d for d in decades if d["n"] >= FULL_DECADE_DAYS and not d["partial"]), None)
    late = decades[-1]
    if early is None or early is late or late["n"] < 365:
        raise EverydayError("not enough decades to compare")
    ranked = [d for d in decades if d["n"] >= 1000]
    top = max(d["p"] for d in ranked)
    low = min(d["p"] for d in ranked)
    tops = [d["label"] for d in ranked if d["p"] == top]
    lows = [d["label"] for d in ranked if d["p"] == low]
    both = lambda ls: " and the ".join(ls)  # noqa: E731
    p_dec = (f"The {both(tops)} {'have' if len(tops) == 1 and tops[0].endswith('so far') else 'had' if len(tops) == 1 else 'share'} "
             f"the highest share of above-normal days of any decade, {top}%"
             f"{'' if len(tops) == 1 else ' each'}, and the {both(lows)} the lowest, {low}%. "
             f"Across the whole record, highs averaged {abs(round(float(x['a'].mean()), 1)):.1f}°F "
             f"{'below' if x['a'].mean() < 0 else 'above'} today’s normal.")
    lede = (f"Since {y0}, {possessive(C)} daily high has been above the 1991–2020 normal for the date on "
            f"{p_all}% of {n_all:,} days. That share was {early['p']}% in the {early['label']}"
            f" and is {late['p']}% in the {late['label'].replace(' so far', '')} so far.")

    # ---- 2. The newest square ----
    newest = x[x["D"].dt.date == asof].iloc[0]
    n_hi, n_norm = int(newest["Hi"]), float(newest["norm"])
    diff = n_hi - whole(n_norm)
    side = "above" if diff > 0 else "below" if diff < 0 else "right at"
    gap = f"{abs(diff)}° {side}" if diff else "right at"
    p_new = (f"The latest day on the chart, {long_date(asof)}, had a high of {f_deg(n_hi)}°F, {gap} the normal of "
             f"{f_deg(whole(n_norm))}°F for the date.")

    # ---- 3. Warmest and coolest complete years ----
    yrs = x[x["Y"] < yr].groupby("Y").agg(n=("a", "size"), a=("a", "mean"), p=("up", "mean"))
    yrs = yrs[yrs["n"] >= FULL_YEAR_DAYS]
    if len(yrs) < 30:
        raise EverydayError("fewer than 30 complete years (methods.md)")
    warm_y = int(yrs["a"].idxmax())
    cool_y = int(yrs["a"].idxmin())
    w_a, c_a = round(float(yrs.loc[warm_y, "a"]), 1), round(float(yrs.loc[cool_y, "a"]), 1)
    w_p, c_p = round(100 * float(yrs.loc[warm_y, "p"])), round(100 * float(yrs.loc[cool_y, "p"]))
    vs = lambda v: f"{abs(v):.1f}°F {'above' if v > 0 else 'below'} normal" if v else "right at normal"  # noqa: E731
    p_years = (f"Of {len(yrs)} complete years since {y0}, {warm_y} ran warmest: its highs averaged {vs(w_a)}, and "
               f"{w_p}% of its days were above normal. {cool_y} ran coolest: its highs averaged {vs(c_a)}, with "
               f"{c_p}% of days above.")

    # ---- 4. The longest runs above and below normal (a missing day ends a run) ----
    s = x.set_index("D")["up"].sort_index()
    s_dn = (x.set_index("D")["a"] < 0).sort_index()   # strictly below; a day exactly at normal ends both kinds of run

    def longest(series):
        best, cur, start, span, prev = 0, 0, None, None, None
        for d, hit in series.items():
            d = d.date()
            if hit and prev is not None and cur and (d - prev).days == 1:
                cur += 1
            elif hit:
                cur, start = 1, d
            else:
                cur = 0
            if hit and cur >= best:
                best, span = cur, (start, d)
            prev = d
        return best, span

    best, (st_a, st_b) = longest(s)
    cold, (cd_a, cd_b) = longest(s_dn)
    p_streak = (f"The longest run of above-normal highs lasted {best} days, from {long_date(st_a)}, to "
                f"{long_date(st_b)}{'; it is still going' if st_b == asof else ''}. The longest run below normal lasted "
                f"{cold} days, from {long_date(cd_a)}, to {long_date(cd_b)}{'; it is still going' if cd_b == asof else ''}.")

    # ---- 4b. Month by month: the first 30 years on the chart against the last 30 complete years ----
    first30 = (y0, y0 + 29)
    last30 = (yr - 30, yr - 1)
    months = []
    if last30[0] > first30[1]:
        x["mo"] = x["MD"].str[:2].astype(int)
        for m in range(1, 13):
            a_ = x[(x["mo"] == m) & x["Y"].between(*first30)]
            b_ = x[(x["mo"] == m) & x["Y"].between(*last30)]
            if len(a_) < 600 or len(b_) < 600:      # about 20 years of that month's days
                months = []
                break
            months.append({"m": m, "n0": int(len(a_)), "p0": round(100 * a_["up"].mean()), "a0": round(float(a_["a"].mean()), 1),
                           "n1": int(len(b_)), "p1": round(100 * b_["up"].mean()), "a1": round(float(b_["a"].mean()), 1)})
    p_months = ""
    if months:
        ch = sorted(months, key=lambda r: (r["p1"] - r["p0"], -r["m"]))
        hi_m, lo_m = ch[-1], ch[0]
        n_up = sum(1 for r in months if r["p1"] > r["p0"])
        p_months = (f"In {n_up} of 12 months, more days beat normal in {last30[0]}–{last30[1]} than in {first30[0]}–{first30[1]}. "
                    f"The biggest change came in {MONTHS[hi_m['m'] - 1]}, from {hi_m['p0']}% of days to {hi_m['p1']}%"
                    + (f"; the least in {MONTHS[lo_m['m'] - 1]}, from {lo_m['p0']}% to {lo_m['p1']}%." if lo_m is not hi_m else "."))

    # ---- 5. This year so far, against the same days of every other year ----
    md_end = asof.strftime("%m-%d")
    win = x[x["MD"] <= md_end]
    window_days = idx_of(md_end) + 1   # leap-year days, so years with Feb 29 aren't short-changed
    by_y = win.groupby("Y").agg(n=("up", "size"), up=("up", "sum"))
    ok = by_y[by_y["n"] >= MIN_WINDOW_SHARE * (window_days - (0 if md_end < "02-29" else 1))]
    p_year = ""
    year_rank = None
    if yr in ok.index and window_days >= 31 and len(ok) >= 30:
        share = ok["up"] / ok["n"]
        mine = float(share[yr])
        k = int((share.drop(yr) > mine).sum()) + 1
        year_rank = {"p": round(100 * mine), "rank": k, "years": int(len(ok)), "n": int(ok.loc[yr, "n"])}
        p_year = (f"So far in {yr}, through {asof:%B} {asof.day}, {year_rank['p']}% of days have had a high above normal, "
                  f"the {rank_words(k)} share for that stretch of the year in {len(ok)} years since {y0}.")

    # ---- encode, then check every sentence ----
    rows, exc = encode(h, y0, yr, norm)
    dec = decode(rows, exc, y0, norm)
    raw = {(int(y), idx_of(md)): int(v) for y, md, v in zip(x["Y"], x["MD"], x["Hi"])}
    from_page_up = sum(1 for (y, i), v in dec.items() if v > norm[i])
    streak_check, run, last = 0, 0, None
    for d in sorted(dec):
        y, i = d
        day = date(2000, 1, 1) + timedelta(days=i)
        if i == 59 and y % 4:      # Feb 29 in a non-leap year can't hold a reading
            raise EverydayError("a reading on a date that doesn't exist")
        real = date(y, day.month, day.day)
        if dec[d] > norm[i]:
            run = run + 1 if last is not None and (real - last).days == 1 and run else 1
            streak_check = max(streak_check, run)
        else:
            run = 0
        last = real
    mcount = {}   # (0 = first 30 years, 1 = last 30 complete years, month) -> [days, days above normal], from the page's data
    mstart = [idx_of(f"{m:02d}-01") for m in range(1, 13)]
    for (y, i), v in dec.items():
        w = 0 if first30[0] <= y <= first30[1] else 1 if last30[0] <= y <= last30[1] else None
        if w is None:
            continue
        m = max(k for k in range(12) if mstart[k] <= i) + 1
        c_ = mcount.setdefault((w, m), [0, 0])
        c_[0] += 1
        c_[1] += v > norm[i]
    rc_keys = [k for k in dec if 2020 <= k[0] < yr]
    recent = {"first": 2020, "last": yr - 1, "n": len(rc_keys),
              "p": round(100 * sum(1 for k in rc_keys if dec[k] > norm[k[1]]) / len(rc_keys)) if rc_keys else None}
    title = f"{C} Daily Temperature History"
    if len(title + SUFFIX) > TITLE_MAX:
        title = f"{C} Daily Temperatures"
    if len(title + SUFFIX) > TITLE_MAX:
        title = f"{C}: Every Day"
    description = (f"Every day since {y0} in {C}, {city['st']}, colored by the day's high against normal. "
                   f"{late['p']}% of days since {late['first']} were above it, against {early['p']}% in the {early['label']}.")
    if len(description) > 160:
        description = (f"Every day since {y0} in {C}, colored by the high against normal: {late['p']}% above it since "
                       f"{late['first']}, {early['p']}% in the {early['label']}.")
    claims = {
        "Every high on the page matches the record": dec == raw,
        "Share above normal recounted from the page's data": round(100 * from_page_up / len(dec)) == p_all and len(dec) == n_all,
        "Decades add up to the whole": sum(d["n"] for d in decades) == n_all and sum(d["up"] for d in decades) == up_all,
        "Decade shares recounted": all(round(100 * d["up"] / d["n"]) == d["p"] for d in decades),
        "Highest and lowest decades recounted": all(low <= d["p"] <= top for d in ranked) and top > low,
        "Newest square is the newest day": max(dec) == (yr, idx_of(md_end)) and dec[max(dec)] == n_hi,
        "Longest run recounted from the page's data": streak_check == best,
        "Longest run is above normal every day": all(bool(v) for v in s[str(st_a):str(st_b)].values)
                                                  and len(s[str(st_a):str(st_b)]) == best,
        "Longest cool run is below normal every day": all(bool(v) for v in s_dn[str(cd_a):str(cd_b)].values)
                                                      and len(s_dn[str(cd_a):str(cd_b)]) == cold,
        "Month shares recounted from the page's data": all(
            round(100 * mcount[(w, r["m"])][1] / mcount[(w, r["m"])][0]) == r[key]
            for r in months for w, key in ((0, "p0"), (1, "p1"))),
        "Warmest year is warmer than the coolest": w_a > c_a,
        "Year-so-far rank recounted": year_rank is None or year_rank["rank"] == 1 + sum(
            1 for y in ok.index if y != yr and ok.loc[y, "up"] / ok.loc[y, "n"] > ok.loc[yr, "up"] / ok.loc[yr, "n"]),
        "Title fits": len(title + SUFFIX) <= TITLE_MAX,
        "Description is 110-160 characters": 110 <= len(description) <= 160,
    }
    broken = [k for k, okk in claims.items() if not okk]
    if broken:
        raise EverydayError("; ".join(broken))

    return {
        "slug": slug, "city": city, "asof": ov["asof"], "built": today.isoformat(), "year": yr, "y0": y0,
        "record_first": int(ov["first_year"]), "title": title, "description": description,
        "n": n_all, "p": p_all, "decades": decades, "early": early["label"], "late": late["label"],
        "warm": {"y": warm_y, "a": w_a, "p": w_p}, "cool": {"y": cool_y, "a": c_a, "p": c_p}, "complete_years": int(len(yrs)),
        "streak": {"n": best, "from": st_a.isoformat(), "to": st_b.isoformat()},
        "cold": {"n": cold, "from": cd_a.isoformat(), "to": cd_b.isoformat()},
        "months": months, "first30": first30, "last30": last30, "recent": recent,
        "year_rank": year_rank,
        "newest": {"date": ov["asof"], "hi": n_hi, "norm": round(n_norm, 1)},
        "text": {"lede": lede, "p_dec": p_dec, "p_new": p_new, "p_years": p_years, "p_streak": p_streak, "p_year": p_year,
                 "p_months": p_months},
        "payload": {"slug": slug, "name": C, "st": city["st"], "y0": y0, "asof": ov["asof"],
                    "alpha": ALPHA, "dmin": D_MIN, "norm": norm, "rows": rows, "exc": exc},
    }


# ------------------------------------------------------------------ HTML
def decade_rows_html(ed):
    out = []
    for d in ed["decades"]:
        out.append(f'<tr><td>{html.escape(d["label"])}</td><td>{d["n"]:,}</td><td>{d["p"]}%</td>'
                   f'<td>{f_deg(d["a"], True)}°F</td></tr>')
    return "\n".join(out)


def tabs_html(slug, name):
    """The city's Almanac pages as plain links, this one current (same markup as records.tabs_html)."""
    pages = [("../", "Overview", "Yesterday and trends"), ("../records/", "Records", "Highs and lows by date"),
             ("./", "Every day", "A square for every day")]
    links = "".join(f'<a href="{href}"{" aria-current=\"page\"" if href == "./" else ""}><b>{n}</b><span>{s}</span></a>'
                    for href, n, s in pages)
    return f'<nav class="tabs" aria-label="{html.escape(name)} Almanac pages">{links}</nav>'


def places_html(eds, prefix):
    """Every place with an every-day page, by state, as plain links."""
    by_state = {}
    for e in eds:
        by_state.setdefault(e["city"]["state"], []).append((e["city"]["name"], e["slug"]))
    return "".join(
        f'<div class="st"><h3>{html.escape(st)}</h3><ul>'
        + "".join(f'<li><a href="{prefix}{s}/every-day/">{html.escape(n)}</a></li>' for n, s in sorted(ps))
        + "</ul></div>" for st, ps in sorted(by_state.items()))


def picker_json(eds):
    return json.dumps(sorted([[e["slug"], e["city"]["name"], e["city"]["st"]] for e in eds], key=lambda r: (r[1], r[2])),
                      separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")


def steps(ed, is_hub):
    """The five-step story: (question, text HTML, what the picture shows). All five go into the HTML."""
    c, t, name, yr, y0 = ed["city"], ed["text"], ed["city"]["name"], ed["year"], ed["y0"]
    decs = {d["label"]: d for d in ed["decades"]}
    early = decs[ed["early"]]
    last_full = [d for d in ed["decades"] if not d["partial"]][-1]
    rc = ed["recent"]
    asof = date.fromisoformat(ed["asof"])
    e = html.escape
    s1 = (e(f"Each row is a year since {y0} and each square a day: red where the high beat the 1991–2020 normal for "
            "that date, blue where it fell short, darker the closer it came. ")
          + f'<span class="pNew">{e(t["p_new"])}</span>'
          + e(" Hover over any square, or tap it on a phone, for that day; the column at the right is each year’s average."))
    s2 = e(f"In the {early['label']}, {early['p']}% of days beat today’s normal high. In the {last_full['label']}, "
           f"{last_full['p']}%" + (f"; in {rc['first']}–{rc['last']}, {rc['p']}%." if rc["p"] is not None and rc["last"] > last_full["last"] else ".")
           + " Where the rows turn redder toward the bottom, the typical day has warmed.")
    bands = [[early["first"], early["last"], f"{early['label']}: {early['p']}% above normal"],
             [last_full["first"], last_full["last"], f"{last_full['label']}: {last_full['p']}%"]]
    if rc["p"] is not None and rc["last"] > last_full["last"]:
        bands.append([rc["first"], rc["last"], f"{rc['first']}–{rc['last']}: {rc['p']}%"])
    w, cl = ed["warm"], ed["cool"]
    streak_txt = t["p_streak"]
    if is_hub:   # the front page is frozen when copied, so an unfinished run carries its date
        streak_txt = streak_txt.replace("; it is still going", f"; it was still going on {short_date(asof)}")
    st, cd = ed["streak"], ed["cold"]
    out = [
        (f"What does every day since {y0} in {name} look like?", s1, {"ring": True}),
        (f"Has the typical day in {name} warmed?", s2, {"bands": bands, "rows": [b[:2] for b in bands]}),
        ("Which years ran warmest and coolest?", e(t["p_years"]),
         {"rows": [[w["y"], w["y"]], [cl["y"], cl["y"]]],
          "labels": [[w["y"], f"{w['y']}: warmest, {f_deg(w['a'], True)}°F"], [cl["y"], f"{cl['y']}: coolest, {f_deg(cl['a'], True)}°F"]]}),
        ("How long can a warm or cool spell last?", e(streak_txt),
         {"rows": [[int(st["from"][:4]), int(st["to"][:4])], [int(cd["from"][:4]), int(cd["to"][:4])]],
          "spans": [[st["from"], st["to"], f"{st['n']} days above normal"], [cd["from"], cd["to"], f"{cd['n']} days below"]]}),
    ]
    if is_hub:
        out.append(("What about your city?", e(
            f"Search for any of the Almanac’s {ed['places']} places in the box above, or pick one from the list below. "
            "Each has its own page like this one, rebuilt every morning from its official NOAA record."), {"ring": True}))
    elif t["p_year"]:
        yrk = ed["year_rank"]
        out.append((f"How does {yr} compare so far?", e(t["p_year"]),
                    {"rows": [[yr, yr]], "labels": [[yr, f"{yr} so far: {yrk['p']}% above normal"]]}))
    else:
        out.append((f"How does {yr} compare so far?", e(f"It’s too early in {yr} to compare it with other years; "
                    "come back after January. The outlined square is the latest day."), {"ring": True}))
    return out


def steps_html(st):
    return "\n".join(
        f'    <article class="step{" on" if j == 0 else ""}" id="step-{j + 1}"><p class="step-k">{j + 1} of {len(st)}</p>'
        f'<h2>{html.escape(q)}</h2><p>{text}</p></article>' for j, (q, text, _) in enumerate(st))


def facts_rows(ed, is_hub):
    c, rc = ed["city"], ed["recent"]
    early = next(d for d in ed["decades"] if d["label"] == ed["early"])
    last_full = [d for d in ed["decades"] if not d["partial"]][-1]
    w, cl, st, cd = ed["warm"], ed["cool"], ed["streak"], ed["cold"]
    rows = [
        ("Official record", f"{c['record_name']}, from {ed['record_first']}"),
        ("Chart starts", f"{ed['y0']}" + ("" if ed["y0"] == 1900 else " (the record’s first full year)")),
        ("Days on the chart", f"{ed['n']:,} with a high, through {short_date(date.fromisoformat(ed['asof']))}"),
        ("Days above the 1991–2020 normal", f"{ed['p']}%"),
        (f"In the {early['label']}", f"{early['p']}% of {early['n']:,} days"),
        (f"In the {last_full['label']}", f"{last_full['p']}% of {last_full['n']:,} days"),
    ]
    if rc["p"] is not None and rc["last"] > last_full["last"]:
        rows.append((f"In {rc['first']}–{rc['last']}", f"{rc['p']}% of {rc['n']:,} days"))
    rows += [
        ("Warmest complete year", f"{w['y']}: highs {f_deg(w['a'], True)}°F against normal, {w['p']}% of days above"),
        ("Coolest complete year", f"{cl['y']}: highs {f_deg(cl['a'], True)}°F against normal, {cl['p']}% of days above"),
        ("Longest run above normal", f"{st['n']} days, {short_date(date.fromisoformat(st['from']))} to {short_date(date.fromisoformat(st['to']))}"),
        ("Longest run below normal", f"{cd['n']} days, {short_date(date.fromisoformat(cd['from']))} to {short_date(date.fromisoformat(cd['to']))}"),
    ]
    if not is_hub:
        nw = ed["newest"]
        rows.append(("Latest day", f"{short_date(date.fromisoformat(nw['date']))}: high {f_deg(nw['hi'])}°F, normal {f_deg(whole(nw['norm']))}°F"))
    return "\n".join(f"<tr><th scope=\"row\">{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in rows)


def month_section(ed):
    if not ed["months"]:
        return ""
    (a0, a1), (b0, b1) = ed["first30"], ed["last30"]
    rows = "\n".join(
        f'<tr><td>{MONTHS[r["m"] - 1]}</td><td>{r["p0"]}%</td><td>{r["p1"]}%</td><td>{"+" if r["p1"] > r["p0"] else "−" if r["p1"] < r["p0"] else ""}{abs(r["p1"] - r["p0"])}</td>'
        f'<td>{f_deg(r["a0"], True)}°F</td><td>{f_deg(r["a1"], True)}°F</td></tr>' for r in ed["months"])
    return (f'  <h2 class="sec">Which months changed most?</h2>\n  <p class="prose">{html.escape(ed["text"]["p_months"])}</p>\n'
            f'  <div class="tw"><table id="months">\n    <caption>Share of days with a high above the 1991–2020 normal, and the average '
            f'high against normal, {a0}–{a1} and {b0}–{b1}</caption>\n'
            f'    <thead><tr><th>Month</th><th>{a0}–{a1}</th><th>{b0}–{b1}</th><th>Change (points)</th><th>Avg {a0}–{a1}</th><th>Avg {b0}–{b1}</th></tr></thead>\n'
            f'    <tbody>\n{rows}\n    </tbody></table></div>')


def render(ed, *, seo_head, nearby, main_site, hub=None):
    """One page from everyday_template.html. hub: None for a city page, or a dict for the showpiece front page
    ({"places": [...every built ed...], "live_url": relative URL of the featured city's days.json})."""
    tpl = (HERE / "everyday_template.html").read_text(encoding="utf-8")
    c, t = ed["city"], ed["text"]
    asof = date.fromisoformat(ed["asof"])
    name = c["name"]
    is_hub = hub is not None
    almanac = "../almanac/" if is_hub else "../../"          # from the page to the Almanac's front folder
    city_url = f"{almanac}{ed['slug']}/"
    ed["places"] = len(hub["places"]) if is_hub else len(hub_places_for(ed))
    st = steps(ed, is_hub)
    sections = {
        "H_DECADES": f"How often has {possessive(name)} high been above normal, decade by decade?",
        "H_YEAR": f"How does {ed['year']} compare so far?",
    }
    fill = {
        "TITLE": html.escape(("Historical Weather: Every Day Since 1900" if is_hub else ed["title"]) + SUFFIX),
        "DESCRIPTION": html.escape(hub["description"] if is_hub else ed["description"], quote=True),
        "HEAD_SEO": seo_head, "MAIN": main_site,
        "BODY_CLASS": "hub" if is_hub else "city",
        "CRUMBS": (f'<a href="{main_site}">Home</a> › <a href="../">Weather</a> › Every day since 1900' if is_hub else
                   f'<a href="{main_site}">Home</a> › <a href="{main_site}weather/">Weather</a> › '
                   f'<a href="../../">Almanac</a> › <a href="../">{html.escape(name)}</a> › Every day'),
        "TABS": "" if is_hub else tabs_html(ed["slug"], name),
        "H1": ("How warm was every day since 1900 <span>in your city?</span>" if is_hub else
               f"How warm was every day since {ed['y0']} <span>in {html.escape(name)}?</span>"),
        "ANSWER": html.escape(hub["answer"] if is_hub else t["lede"]),
        "DECK": html.escape(hub["deck"] if is_hub else
                            "Each row is a year and each square a day, colored by how far the day’s high was from "
                            "the normal for that date: red warmer, blue cooler, dark close to normal. Hover or tap any "
                            "square to read it. A new square fills in every morning."),
        "P_NEW": html.escape(t["p_new"]),
        "LATEST": f'<time class="latestT" datetime="{ed["asof"]}">{short_date(asof)}</time>',
        "BYLINE": (f'By Shawn Nafziger · Published <time datetime="{hub["published"]}">{hub["published_words"]}</time> · '
                   f'Featured: {html.escape(name)}, data through <time id="asofT" datetime="{ed["asof"]}">{short_date(asof)}</time>'
                   if is_hub else
                   (ed.get("published_html", "") + " · " if ed.get("published_html") else "") +
                   f'Data through <time datetime="{ed["asof"]}">{short_date(asof)}</time> · NOAA official daily record · '
                   'Updated every morning'),
        "PICKER_NOTE": ("Featured: " + html.escape(f"{name}, {c['st']}") + ". Pick another place to open its own page."
                        if is_hub else "See another place:"),
        # Only readers without scripts see the still; with scripts it's hidden and, being lazy, never downloaded.
        "STILL": (f'<img id="still" src="{hub["still"]}" width="1136" height="{hub.get("still_h", 672)}" loading="lazy" alt="{html.escape(hub["still_alt"], quote=True)}">'
                  if is_hub else ""),
        "CITY": html.escape(name), "ST": c["st"], "STATE": html.escape(c["state"]),
        "Y0": str(ed["y0"]), "YEAR": str(ed["year"]),
        "H_DECADES": html.escape(sections["H_DECADES"]),
        # The front page is a copy frozen when it's published, so its "so far" numbers carry their date.
        "P_DECADES": html.escape((f"Through {long_date(asof)}: " if is_hub else "") + t["p_dec"]),
        "THROUGH": f" (through {short_date(asof)})" if is_hub else "",
        "DECADE_ROWS": decade_rows_html(ed),
        "CITY_LINKS": (f'<a href="{city_url}">{html.escape(name)} weather history<span>Yesterday in context, the year so far and how the climate has changed.</span></a>'
                       f'<a href="{city_url}records/">{html.escape(name)} record highs and lows<span>Every date’s record high and low and the years that set them.</span></a>'
                       + ("" if is_hub else f'<a href="{main_site}{HUB_PATH}">How warm was every day since 1900 in your city?<span>The showpiece: pick any of the Almanac’s places.</span></a>')),
        "NEARBY": nearby,
        "PLACES": places_html(hub["places"], almanac) if is_hub else "",
        "PICKER": picker_json(hub["places"]) if is_hub else picker_json(hub_places_for(ed)),
        "ALMANAC_REL": almanac,
        "RECORD_NAME": html.escape(c["record_name"]), "CAVEAT": html.escape(c["caveat"]),
        "LIVE_URL": hub["live_url"] if is_hub else "",
        "DATA": json.dumps(ed["payload"], separators=(",", ":")).replace("</", "<\\/"),
        "STEPS": steps_html(st),
        "STEPS_JSON": json.dumps([{"q": q, "focus": f} for q, _, f in st], separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/"),
        "PLACE_COUNT": str(ed["places"]),
        "H_FACTS": "Key facts", "FACTS_ROWS": facts_rows(ed, is_hub),
        "FACTS_CAPTION": html.escape(f"{name}, {c['state']}: daily highs against the smoothed 1991–2020 normal for the date"
                                     + (f", through {short_date(asof)}" if is_hub else "")),
        "MONTH_SECTION": month_section(ed),
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    left = [p for p in tpl.split("{{")[1:] if "}}" in p[:40]]
    if left:
        raise RuntimeError(f"Unfilled placeholders on the every-day page: {[p.split('}}')[0] for p in left]}")
    return tpl


_PLACES = []   # set by build.py once every place has been tried, for the city pages' "see another place" picker


def hub_places_for(ed):
    return _PLACES or [ed]


def payload_json(ed):
    return json.dumps(ed["payload"], separators=(",", ":"))


# ------------------------------------------------------------------ the showpiece front page
def hub_words(ed, published):
    c, name = ed["city"], ed["city"]["name"]
    early = next(d for d in ed["decades"] if d["label"] == ed["early"])
    last_full = [d for d in ed["decades"] if not d["partial"]][-1]
    rc = ed["recent"]
    span = f"{rc['first']}–{rc['last']}"
    # Complete years only, so this text (frozen when the page is copied to the site) never goes stale.
    answer = (f"In {name}, {c['state']}, {early['p']}% of days in the {early['label']} had a high above today’s normal "
              f"for the date. In the {last_full['label']} it was {last_full['p']}%, and in {span}, {rc['p']}%. Every square "
              f"below is one day since {ed['y0']}, colored by how far its high was from normal, and a new day is added each morning.")
    description = (f"Every day since 1900 in 300+ US cities, colored by the high against normal. In {name}, "
                   f"{early['p']}% of days in the {early['label']} beat today’s normal; in {span}, {rc['p']}%.")
    deck = ("Pick from more than 300 places in the Weather Almanac. Each row is a year and each square a day: red warmer "
            "than the 1991–2020 normal for that date, blue cooler, dark close to normal. Hover or tap any square to read it.")
    if not 110 <= len(description) <= 160:
        raise EverydayError(f"front-page description is {len(description)} characters")
    return {"answer": answer, "description": description, "deck": deck, "published": published.isoformat(),
            "published_words": short_date(published), "last_full": last_full, "recent": rc, "early": early}


def main():
    sys.path.insert(0, str(HERE))
    import build  # noqa: E402
    ap = argparse.ArgumentParser(description="Build the showpiece front page for the main site.")
    ap.add_argument("--showpiece", required=True, help="the featured place's slug, e.g. chicago")
    ap.add_argument("out", help="folder to write index.html (and the still image) into")
    ap.add_argument("--published", default=date.today().isoformat())
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    eds = []
    for slug in [s for s in build.CITIES if build.store.exists(build.HISTORY_DIR, s)
                 and __import__("records").has_branch_pages(s, build.CITIES)]:
        try:
            h = build.load(slug)
            eds.append(build_everyday(build.build(slug, h=h), h, build.CITIES))
        except Exception as e:  # noqa: BLE001 (same rule as the daily run: a place that fails is left out)
            print(f"{slug:<20} left out: {e}")
    ed = next(e for e in eds if e["slug"] == a.showpiece)
    words = hub_words(ed, date.fromisoformat(a.published))
    still = "chart-" + ed["slug"] + ".webp"
    rc = words["recent"]
    still_alt = (f"Every day since {ed['y0']} in {ed['city']['name']}, {ed['city']['state']}, as a grid: one row per year, "
                 f"one square per day, red where the high was above the 1991–2020 normal and blue where below. The rows "
                 f"turn redder toward the bottom: {words['early']['p']}% of days were above normal in the {ed['early']}, "
                 f"{rc['p']}% in {rc['first']}–{rc['last']}.")
    hub = {**words, "places": eds, "still": still, "still_alt": still_alt,
           "live_url": f"../almanac/{ed['slug']}/every-day/days.json"}
    url = build.MAIN_SITE + HUB_PATH
    a_ = lambda v: html.escape(v, quote=True)  # noqa: E731
    share = build.MAIN_SITE + HUB_PATH + "share.png"
    head = "\n".join([
        f'<link rel="canonical" href="{url}">', '<meta property="og:type" content="article">',
        '<meta property="og:site_name" content="Tall Order Digital">',
        '<meta property="og:title" content="How warm was every day since 1900 in your city?">',
        f'<meta property="og:description" content="{a_(words["description"])}">', f'<meta property="og:url" content="{url}">',
        f'<meta property="og:image" content="{share}">', '<meta property="og:image:width" content="1200">',
        '<meta property="og:image:height" content="630">', f'<meta property="og:image:alt" content="{a_(still_alt)}">',
        '<meta name="twitter:card" content="summary_large_image">',
        '<meta name="twitter:title" content="How warm was every day since 1900 in your city?">',
        f'<meta name="twitter:description" content="{a_(words["description"])}">', f'<meta name="twitter:image" content="{share}">',
        f'<meta name="twitter:image:alt" content="{a_(still_alt)}">'])
    page = render(ed, seo_head=head, nearby="", main_site="../../", hub=hub)
    (out / "index.html").write_text(page, encoding="utf-8")
    (out / "facts.json").write_text(json.dumps({k: ed[k] for k in ("slug", "asof", "y0", "decades", "warm", "cool", "streak")}
                                               | {"answer": words["answer"], "description": words["description"], "recent": words["recent"],
                                                  "still_alt": still_alt, "places": len(eds)}, indent=1), encoding="utf-8")
    (out / "days.json").write_text(payload_json(ed), encoding="utf-8")
    print(f"Showpiece page written to {out} (featuring {ed['city']['name']}, {len(eds)} places linked).")
    print(f"Description: {len(words['description'])} characters")


if __name__ == "__main__":
    main()
