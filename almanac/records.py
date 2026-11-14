"""
Records pages: one per place, at weather/almanac/<place>/records/ (Nov 12, 2026).

Built by build.py right after each place's overview page, from the same official record:
every date's record high and low (with the year or years that set it), each month's records,
the all-time extremes, and a chart of the whole year. The overview keeps the story (records
by decade, trends); this page is the lookup ("chicago record low temperature").

Same rules as the overview: every sentence is chosen from the place's own numbers and checked
before the page is written. If a check fails, the records page isn't written that day, and
carry_over.py keeps the last good one.

Plan and decisions: the "Weather intelligence" Project doc almanac-branch-pages-proposal.md.
"""

import html
import json
import re
from datetime import date
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December"]
MIN_YEARS_PER_DATE = 30      # methods.md: a same-date sample needs at least 30 years
TITLE_MAX = 62               # seo-standards.md: about 50-60 characters, " · Tall Order Digital" included
SUFFIX = " · Tall Order Digital"
STORY_RECORD_LOWS = ("Record lows are disappearing", "weather/record-lows-disappearing/")


class RecordsError(Exception):
    """A sentence the records page would print is not true for this place's data."""


# ------------------------------------------------------------------ who gets a records page
def has_branch_pages(slug, cities):
    """Second stations in a city that already has its official record (New York (JFK), Dallas (Love Field),
    Washington (Dulles): same city name, within 60 miles) get no branch pages: they would chase the same searches as the city's own page. Their overview stays."""
    c = cities[slug]
    if "(" not in c["name"]:
        return True
    base = re.sub(r"\s*\(.*\)", "", c["name"]).strip()
    return not any(s != slug and o["name"] == base and _miles(c, o) <= 60 for s, o in cities.items())


def _miles(a, b):
    from math import asin, cos, radians, sin, sqrt
    la1, lo1, la2, lo2 = map(radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    x = sin((la2 - la1) / 2) ** 2 + cos(la1) * cos(la2) * sin((lo2 - lo1) / 2) ** 2
    return 3958.8 * 2 * asin(sqrt(x))


# ------------------------------------------------------------------ words
def f_deg(v):
    return f"−{abs(int(v))}" if v < 0 else str(int(v))


def long_date(d):
    return f"{d:%B} {d.day}, {d.year}"


def short_date(d):
    return f"{d:%b} {d.day}, {d.year}"


def and_list(items):
    items = list(items)
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def dates_words(ds, fmt=long_date, most=3):
    """'January 20, 1985' / 'December 24, 1983, and January 18, 1994' / '4 days, most recently ...'."""
    ds = sorted(ds)
    if len(ds) <= 2:
        return ", and ".join(fmt(d) for d in ds) if len(ds) == 2 else fmt(ds[0])
    if len(ds) <= most:
        return and_list(fmt(d) for d in ds)
    return f"{len(ds)} days, most recently {fmt(ds[-1])}"


def years_words(ys):
    return and_list(str(y) for y in sorted(ys))


def possessive(name):
    return name + ("'" if name.endswith("s") else "'s")


# ------------------------------------------------------------------ the numbers
def build_records(ov, h, cities, today=None):
    """ov: the overview's data (build.build); h: the same loaded record. Returns everything the page shows."""
    today = today or date.today()
    slug, city = ov["slug"], ov["city"]
    C, Cs = city["name"], possessive(city["name"])
    asof = date.fromisoformat(ov["asof"])
    H = h.dropna(subset=["Hi"])
    L = h.dropna(subset=["Lo"])
    if H.empty or L.empty:
        raise RecordsError("no temperatures")
    temp_first = int(min(H["Y"].min(), L["Y"].min()))
    temp_years = int(pd.concat([H["Y"], L["Y"]]).nunique())

    def days_of(df, col, v):
        return [d.date() for d in df.loc[df[col] == v, "D"]]

    # ---- all-time ----
    hi_max, lo_min = int(H["Hi"].max()), int(L["Lo"].min())
    hi_low, lo_high = int(H["Hi"].min()), int(L["Lo"].max())
    alltime = {
        "hi": {"v": hi_max, "days": days_of(H, "Hi", hi_max)},
        "lo": {"v": lo_min, "days": days_of(L, "Lo", lo_min)},
        "cold_day": {"v": hi_low, "days": days_of(H, "Hi", hi_low)},
        "warm_night": {"v": lo_high, "days": days_of(L, "Lo", lo_high)},
    }
    P = h.dropna(subset=["Pr"])
    if len(P) and P["Pr"].max() > 0:
        pmax = round(float(P["Pr"].max()), 2)
        alltime["wet"] = {"v": pmax, "days": days_of(P.assign(Pr=P["Pr"].round(2)), "Pr", pmax),
                          "since": int(P["Y"].min())}
    S = h.dropna(subset=["Sn"])
    if len(S) and S["Sn"].max() >= 1.0:   # a place that never measured an inch of snow in a day gets no snow tile
        smax = round(float(S["Sn"].max()), 1)
        alltime["snow"] = {"v": smax, "days": days_of(S.assign(Sn=S["Sn"].round(1)), "Sn", smax),
                           "since": int(S["Y"].min())}

    # ---- each month ----
    months = []
    for m in range(1, 13):
        a, b = H[H["M"] == m], L[L["M"] == m]
        if a.empty or b.empty:
            raise RecordsError(f"no temperatures in {MONTHS[m - 1]}")
        mh, ml = int(a["Hi"].max()), int(b["Lo"].min())
        months.append({"m": m, "hi": mh, "hi_days": days_of(a, "Hi", mh), "lo": ml, "lo_days": days_of(b, "Lo", ml)})

    # ---- each date (standing records, every year through the latest final day) ----
    norm = dict(zip(ov["season"]["days"], zip(ov["season"]["norm_hi"], ov["season"]["norm_lo"])))
    # grouped once per place (a loop over 366 dates was half the build time)
    hi_rec, lo_rec = H.groupby("MD")["Hi"].max(), L.groupby("MD")["Lo"].min()
    hi_yrs = H[H["Hi"] == H["MD"].map(hi_rec)].groupby("MD")["Y"].agg(lambda s: sorted(int(y) for y in s))
    lo_yrs = L[L["Lo"] == L["MD"].map(lo_rec)].groupby("MD")["Y"].agg(lambda s: sorted(int(y) for y in s))
    n_hi_md, n_lo_md = H.groupby("MD")["Y"].nunique(), L.groupby("MD")["Y"].nunique()
    yrs_md = pd.concat([H[["MD", "Y"]], L[["MD", "Y"]]]).groupby("MD")["Y"].agg(["min", "max"])
    daily = []
    for md in sorted(set(h["MD"])):
        n = int(min(n_hi_md.get(md, 0), n_lo_md.get(md, 0)))
        has = md in hi_rec.index and md in lo_rec.index
        row = {"md": md, "n": n, "first": int(yrs_md.loc[md, "min"]) if md in yrs_md.index else None,
               "last": int(yrs_md.loc[md, "max"]) if md in yrs_md.index else None,
               "shown": has and n >= MIN_YEARS_PER_DATE}
        if has:
            row.update(hi=int(hi_rec[md]), lo=int(lo_rec[md]), hi_y=hi_yrs[md], lo_y=lo_yrs[md])
        nh, nl = norm.get(md, (None, None))
        row["nhi"] = None if nh is None or pd.isna(nh) else int(round(nh))
        row["nlo"] = None if nl is None or pd.isna(nl) else int(round(nl))
        daily.append(row)
    by_md = {r["md"]: r for r in daily}
    missing = [d for d in ov["season"]["days"] if d not in by_md or not by_md[d]["shown"]]
    if missing:
        raise RecordsError(f"{len(missing)} dates have fewer than {MIN_YEARS_PER_DATE} years ({missing[0]}...)")

    # ---- the year in records: where the record high and low are farthest apart and closest ----
    chart_days = [d for d in ov["season"]["days"]]          # 365 days, Feb 29 left out (as the overview's chart)
    gaps = [(by_md[d]["hi"] - by_md[d]["lo"], d) for d in chart_days]
    widest = max(gaps, key=lambda t: (t[0], -chart_days.index(t[1])))
    narrowest = min(gaps, key=lambda t: (t[0], chart_days.index(t[1])))

    # ---- today ----
    t_md = today.strftime("%m-%d")
    t_row = by_md.get(t_md)
    if t_row is not None:
        past = h[(h["MD"] == t_md) & (h["Y"] < today.year)]
        th, tl = past.dropna(subset=["Hi"]), past.dropna(subset=["Lo"])
        t_n = int(min(th["Y"].nunique(), tl["Y"].nunique()))
        yrs_t = pd.concat([th["Y"], tl["Y"]])
        today_rec = {"md": t_md, "n": t_n, "first": int(yrs_t.min()), "last": int(yrs_t.max()),
                     "hi": int(th["Hi"].max()), "hi_y": sorted(int(y) for y in th.loc[th["Hi"] == th["Hi"].max(), "Y"]),
                     "lo": int(tl["Lo"].min()), "lo_y": sorted(int(y) for y in tl.loc[tl["Lo"] == tl["Lo"].min(), "Y"])}
    else:
        today_rec = None

    # ---- this year: daily records set or tied (from the overview's own count) ----
    yr = asof.year
    n_hi, n_lo = len(ov["season"]["rec_hi_days"]), len(ov["season"]["rec_lo_days"])
    ties_hi = sum(1 for r in ov["season"]["rec_hi_days"] if r["tie"])
    ties_lo = sum(1 for r in ov["season"]["rec_lo_days"] if r["tie"])

    # ---- the newest standing record low ----
    newest_lo = max((date(y, int(r["md"][:2]), int(r["md"][3:])) for r in daily if r["shown"] for y in r["lo_y"]))

    # ---- words ----
    tm = MONTHS[asof.month - 1]
    T_lo, T_hi = alltime["lo"], alltime["hi"]
    rec_src = "NOAA's official city record" if city["kind"] == "threadex" else "this station's NOAA record"
    dek = (f"{Cs} record low is <strong class=\"t-lo\">{f_deg(T_lo['v'])}°F, on {dates_words(T_lo['days'])}</strong>, "
           f"and its record high is <strong class=\"t-hi\">{f_deg(T_hi['v'])}°F, on {dates_words(T_hi['days'])}</strong>, "
           f"in {temp_years} years of {rec_src} ({temp_first}–{asof.year}). "
           f"Below: today's records, every month's, and every day's.")

    w_v, w_md = widest
    n_v, n_md = narrowest
    w_d, n_d = date(2001, int(w_md[:2]), int(w_md[3:])), date(2001, int(n_md[:2]), int(n_md[3:]))
    t_year = f"{Cs} records are farthest apart in {MONTHS[w_d.month - 1]}"
    p_year = (f"On {w_d:%B} {w_d.day}, {Cs} record high and record low are <strong>{w_v}°F apart</strong>; "
              f"on {n_d:%B} {n_d.day}, just <strong>{n_v}°F</strong>.")
    if today_rec and today_rec["n"] >= MIN_YEARS_PER_DATE:
        tday = f"{today:%B} {today.day}"
        ordinal_word = {1: "st", 2: "nd", 3: "rd"}.get(today.day % 10 if not 10 <= today.day % 100 <= 20 else 0, "th")
        p_today = (f"Today's records, {tday}: a high of <strong>{f_deg(today_rec['hi'])}°F</strong> "
                   f"({years_words(today_rec['hi_y'])}) and a low of <strong>{f_deg(today_rec['lo'])}°F</strong> "
                   f"({years_words(today_rec['lo_y'])}), from {today_rec['n']} {today:%B} {today.day}{ordinal_word}s, "
                   f"{today_rec['first']} to {today_rec['last']}.")
    else:
        p_today = ""

    cold, warm = alltime["cold_day"], alltime["warm_night"]
    t_all = f"What's the coldest temperature ever recorded in {C}?"
    p_all = (f"The coldest temperature ever recorded in {C} is <strong>{f_deg(T_lo['v'])}°F</strong>, on "
             f"{dates_words(T_lo['days'])}; the hottest is <strong>{f_deg(T_hi['v'])}°F</strong>, on {dates_words(T_hi['days'])}. "
             f"Its coldest day had a high of <strong>{f_deg(cold['v'])}°F</strong> ({dates_words(cold['days'])}), "
             f"and its warmest night never dropped below <strong>{f_deg(warm['v'])}°F</strong> ({dates_words(warm['days'])}).")

    mx = max(months, key=lambda r: r["hi"])
    mn = min(months, key=lambda r: r["lo"])
    cur = months[asof.month - 1]

    def month_years(days):
        return years_words(sorted({d.year for d in days}))

    t_mon = f"What are {Cs} record highs and lows by month?"
    p_mon = (f"They run from <strong>{f_deg(mx['hi'])}°F</strong> in {MONTHS[mx['m'] - 1]} ({month_years(mx['hi_days'])}) "
             f"to <strong>{f_deg(mn['lo'])}°F</strong> in {MONTHS[mn['m'] - 1]} ({month_years(mn['lo_days'])}); "
             f"{possessive(tm)} are {f_deg(cur['hi'])}°F ({month_years(cur['hi_days'])}) and {f_deg(cur['lo'])}°F "
             f"({month_years(cur['lo_days'])}). Where a record is shared, every date is listed.")

    t_day = f"What's {Cs} record high and low for each day?"
    parts = ["Every date's records are below, one table per month"]
    if p_today:
        parts[0] += (f"<span id=\"day-today\">; today, {today:%B} {today.day}, the record high is {f_deg(today_rec['hi'])}°F "
                     f"({years_words(today_rec['hi_y'])}) and the record low {f_deg(today_rec['lo'])}°F "
                     f"({years_words(today_rec['lo_y'])})</span>")
    parts[0] += "."
    parts.append(f"The newest of {Cs} daily record lows dates from <strong>{long_date(newest_lo)}</strong>.")

    def count_words(n, ties, what):
        if n == 0:
            return f"no {what}s"
        tie = "" if ties == 0 else (" (a tie)" if n == 1 else " (one a tie)" if ties == 1 else
                                   " (all ties)" if ties == n else f" ({ties} of them ties)")
        words = ["no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
        return f"<strong>{words[n] if n < 10 else n} daily {what}{'' if n == 1 else 's'}</strong>{tie}"

    hi_w, lo_w = count_words(n_hi, ties_hi, "record high"), count_words(n_lo, ties_lo, "record low")
    lo_w = lo_w.replace("daily ", "", 1) if n_hi and n_lo else lo_w
    parts.append(f"So far in {yr}, {C} has set {hi_w} and {lo_w}.")
    p_day = " ".join(parts)

    title = next(t for t in (f"{C} Record Low and High Temperatures", f"{C} Weather Records", f"{C} Records")
                 if len(t + SUFFIX) <= TITLE_MAX or t == f"{C} Records")
    def when(rec):   # one day: its date; a tie: how many days share it (the page lists them)
        return short_date(rec["days"][0]) if len(rec["days"]) == 1 else f"on {len(rec['days'])} days"
    lo_t, hi_t = f"{f_deg(T_lo['v'])}°F ({when(T_lo)})", f"{f_deg(T_hi['v'])}°F ({when(T_hi)})"
    candidates = [
        f"{Cs} record low is {lo_t} and record high {hi_t}: every day's and month's record since {temp_first} "
        f"from NOAA, updated daily.",
        f"{Cs} record low is {lo_t} and record high {hi_t}: every day's and month's record from NOAA, updated daily.",
        f"{Cs} record low is {lo_t} and record high {hi_t}: every day's record from NOAA, updated daily.",
        f"{Cs} record low is {f_deg(T_lo['v'])}°F and record high {f_deg(T_hi['v'])}°F: every day's and month's record "
        f"from NOAA since {temp_first}, updated daily.",
    ]
    description = next((d for d in candidates if 120 <= len(d) <= 160), candidates[-1])

    # ---- checks: stop instead of publishing something untrue ----
    claims = {
        "All-time high is the record's maximum": T_hi["v"] == int(h["Hi"].max()),
        "All-time low is the record's minimum": T_lo["v"] == int(h["Lo"].min()),
        "Monthly records agree with the all-time records": mx["hi"] == T_hi["v"] and mn["lo"] == T_lo["v"],
        "Daily records agree with the all-time records":
            max(r["hi"] for r in daily if "hi" in r) == T_hi["v"] and min(r["lo"] for r in daily if "lo" in r) == T_lo["v"],
        "Widest gap recounted": by_md[w_md]["hi"] - by_md[w_md]["lo"] == w_v and all(g <= w_v for g, _ in gaps),
        "Narrowest gap recounted": by_md[n_md]["hi"] - by_md[n_md]["lo"] == n_v and all(g >= n_v for g, _ in gaps),
        "Widest and narrowest differ": w_v > n_v,
        "Today's record uses at least 30 years (methods.md)": not p_today or today_rec["n"] >= MIN_YEARS_PER_DATE,
        "Newest record low is a standing record": any(newest_lo.year in r["lo_y"] and r["md"] == newest_lo.strftime("%m-%d") for r in daily),
        "No standing record low is newer": all(y <= newest_lo.year for r in daily for y in r.get("lo_y", [])),
        "This year's record count matches the year's days":
            n_hi == sum(1 for r in ov["season"]["year"] if r[3]) and n_lo == sum(1 for r in ov["season"]["year"] if r[4]),
        "Title fits": len(title + SUFFIX) <= TITLE_MAX or title == f"{C} Records",
        "Description is 110-160 characters": 110 <= len(description) <= 160,
    }
    broken = [k for k, ok in claims.items() if not ok]
    if broken:
        raise RecordsError("; ".join(broken))

    iso = lambda ds: [d.isoformat() for d in ds]  # noqa: E731
    return {
        "slug": slug, "city": city, "asof": ov["asof"], "built": today.isoformat(), "year": yr,
        "first_year": temp_first, "years": temp_years,
        "title": title, "description": description,
        "text": {"dek": dek, "t_year": t_year, "p_year": p_year, "p_today": p_today, "t_all": t_all, "p_all": p_all,
                 "t_mon": t_mon, "p_mon": p_mon, "t_day": t_day, "p_day": p_day},
        "alltime": {k: {**v, "days": iso(v["days"])} for k, v in alltime.items()},
        "months": [{**r, "hi_days": iso(r["hi_days"]), "lo_days": iso(r["lo_days"])} for r in months],
        "daily": daily,
        "today": today_rec,
        "chart": {"days": chart_days, "widest": [w_md, w_v], "narrowest": [n_md, n_v],
                  "rec_hi_days": ov["season"]["rec_hi_days"], "rec_lo_days": ov["season"]["rec_lo_days"]},
    }


# ------------------------------------------------------------------ HTML
def _tile(label, value, when):
    return (f'<div class="tile"><span class="lab">{label}</span><span class="big">{value}</span>'
            f'<span class="when">{when}</span></div>')


def tiles_html(rd):
    A = rd["alltime"]
    D = lambda k: [date.fromisoformat(x) for x in A[k]["days"]]  # noqa: E731
    out = [_tile("Record low", f"{f_deg(A['lo']['v'])}°F", dates_words(D("lo"))),
           _tile("Record high", f"{f_deg(A['hi']['v'])}°F", dates_words(D("hi"))),
           _tile("Coldest day (lowest high)", f"{f_deg(A['cold_day']['v'])}°F", dates_words(D("cold_day"), short_date)),
           _tile("Warmest night (highest low)", f"{f_deg(A['warm_night']['v'])}°F", dates_words(D("warm_night"), short_date))]
    if "wet" in A:
        out.append(_tile("Wettest day", f"{A['wet']['v']:.2f} in", dates_words(D("wet"))))
    if "snow" in A:
        since = f" · measured since {A['snow']['since']}" if A["snow"]["since"] > rd["first_year"] else ""
        out.append(_tile("Snowiest day", f"{A['snow']['v']:.1f} in", dates_words(D("snow")) + since))
    return "\n".join(out)


def months_table_html(rd):
    rows = []
    for r in rd["months"]:
        hd = "<br>".join(short_date(date.fromisoformat(x)) for x in r["hi_days"])
        ld = "<br>".join(short_date(date.fromisoformat(x)) for x in r["lo_days"])
        rows.append(f'<tr><th scope="row">{MONTHS[r["m"] - 1]}</th><td class="num">{f_deg(r["hi"])}°F</td><td class="yr">{hd}</td>'
                    f'<td class="num">{f_deg(r["lo"])}°F</td><td class="yr">{ld}</td></tr>')
    return "\n".join(rows)


def day_tables_html(rd, today):
    by_m = {}
    for r in rd["daily"]:
        by_m.setdefault(int(r["md"][:2]), []).append(r)
    out = []
    for m in range(1, 13):
        rows = []
        for r in by_m.get(m, []):
            d = int(r["md"][3:])
            cls = ' class="today"' if (m, d) == (today.month, today.day) else ""
            label = f"{MONTHS[m - 1][:3]} {d}"
            norm = f"{r['nhi']}° / {r['nlo']}°" if r["nhi"] is not None else "–"
            if r["shown"]:
                rows.append(f'<tr{cls} id="d-{r["md"]}"><th scope="row">{label}</th><td class="num">{f_deg(r["hi"])}°F</td>'
                            f'<td class="yr">{years_words(r["hi_y"]).replace(", and ", ", ").replace(" and ", ", ")}</td>'
                            f'<td class="num">{f_deg(r["lo"])}°F</td>'
                            f'<td class="yr">{years_words(r["lo_y"]).replace(", and ", ", ").replace(" and ", ", ")}</td>'
                            f'<td class="yr">{norm}</td></tr>')
            else:
                rows.append(f'<tr{cls} id="d-{r["md"]}"><th scope="row">{label}</th>'
                            f'<td class="yr" colspan="4">Only {r["n"]} years have this date, too few for a record (30 needed)</td>'
                            f'<td class="yr">{norm}</td></tr>')
        name = MONTHS[m - 1]
        is_open = " open" if m == today.month else ""
        out.append(
            f'<details class="month" id="m-{name.lower()}"{is_open}><summary>{name} <span>{len(rows)} days</span></summary>'
            f'<div class="tbl"><table><caption>{name} daily records in {html.escape(rd["city"]["name"])}</caption>'
            '<thead><tr><th scope="col">Date</th><th scope="col">Record high</th><th scope="col">Year</th>'
            '<th scope="col">Record low</th><th scope="col">Year</th><th scope="col">Normal high / low</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div></details>')
    return "\n".join(out)


def month_links_html(today):
    return "".join(f'<a href="#m-{m.lower()}"{" aria-current=\"true\"" if i + 1 == today.month else ""}>{m[:3]}</a>'
                   for i, m in enumerate(MONTHS))


def today_strip_html(rd, today):
    t = rd["today"]
    if not t or t["n"] < MIN_YEARS_PER_DATE:
        return ""
    return (f'<p class="strip" id="today-strip"><span class="lab">Today · {today:%b} {today.day}</span>'
            f'<span>Record high <b class="t-hi">{f_deg(t["hi"])}°F</b> ({", ".join(map(str, t["hi_y"]))})</span>'
            f'<span>Record low <b class="t-lo">{f_deg(t["lo"])}°F</b> ({", ".join(map(str, t["lo_y"]))})</span>'
            f'<a href="#m-{MONTHS[today.month - 1].lower()}">All of {MONTHS[today.month - 1]} ↓</a></p>')


def tabs_html(slug, current, depth):
    """The city's pages as plain links. depth: '' on the overview, '../' on a branch page."""
    pages = [("", "Overview", "Yesterday and trends"), ("records/", "Records", "Highs and lows by date")]
    links = []
    for path, name, sub in pages:
        href = (depth + path) or "./"
        cur = ' aria-current="page"' if path == current else ""
        links.append(f'<a href="{href}"{cur}><b>{name}</b><span>{sub}</span></a>')
    return f'<nav class="tabs" aria-label="{{CITYNAME}} Almanac pages">{"".join(links)}</nav>'


def render_records(rd, seo_head, nearby, main_site, today=None):
    today = today or date.fromisoformat(rd["built"])
    c, t = rd["city"], rd["text"]
    tpl = (HERE / "records_template.html").read_text(encoding="utf-8")
    asof = date.fromisoformat(rd["asof"])
    story_name, story_path = STORY_RECORD_LOWS
    fill = {
        "TITLE": html.escape(rd["title"]), "DESCRIPTION": html.escape(rd["description"], quote=True),
        "HEAD_SEO": seo_head, "MAIN": main_site,
        "CITY": html.escape(c["name"]), "STATE": html.escape(c["state"]), "ST": c["st"],
        "TABS": tabs_html(rd["slug"], "records/", "../").replace("{CITYNAME}", html.escape(c["name"])),
        "DEK": t["dek"], "ASOF_WORDS": f"{asof:%B} {asof.day}, {asof.year}",
        "T_YEAR": html.escape(t["t_year"]), "P_YEAR": t["p_year"], "P_TODAY": t["p_today"],
        "TODAY_STRIP": today_strip_html(rd, today),
        "T_ALL": html.escape(t["t_all"]), "P_ALL": t["p_all"], "TILES": tiles_html(rd),
        "T_MON": html.escape(t["t_mon"]), "P_MON": t["p_mon"], "MONTH_ROWS": months_table_html(rd),
        "FIRST_YEAR": str(rd["first_year"]), "LAST_YEAR": str(asof.year),
        "T_DAY": html.escape(t["t_day"]), "P_DAY": t["p_day"],
        "MONTH_LINKS": month_links_html(today), "DAY_TABLES": day_tables_html(rd, today),
        "NEARBY": nearby, "STORY": f'<a href="{main_site}{story_path}">{story_name}</a>',
        "RECORD_NAME": html.escape(c["record_name"]), "CAVEAT": html.escape(c["caveat"]),
        "DATA": json.dumps({k: rd[k] for k in ("slug", "asof", "built", "year", "daily", "today", "chart")}
                           | {"city": {"name": c["name"]}}, separators=(",", ":")).replace("</", "<\\/"),
    }
    for k, v in fill.items():
        tpl = tpl.replace("{{" + k + "}}", v)
    left = [p for p in tpl.split("{{")[1:] if "}}" in p[:40]]
    if left:
        raise RuntimeError(f"Unfilled placeholders on the records page: {[p.split('}}')[0] for p in left]}")
    return tpl
