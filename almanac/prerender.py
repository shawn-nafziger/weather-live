"""
prerender.py: write the overview page's script-drawn text into the HTML itself (Nov 24, 2026).

Search engines and AI crawlers read the page as delivered, before any script runs (tallorderdigital-site
docs/seo-standards.md §1: "each chart's ... key numbers as a small HTML table, written into the page, not built
by script"). Until now the overview's nine "Show the data as a table" boxes, the four headline numbers, the
record book's top-10 lists, several chart titles, the year-so-far paragraph and "How this was measured" were
empty in the HTML and filled by the page script. This module produces the same text in Python, from the same
data the page script reads, and build.py writes it in. The script still runs and rewrites each element with
identical content; tests/test_prerender.py checks a few against the page script's own wording.

Each function mirrors one block of the page script in almanac/template.html (same order, same formatting,
including JavaScript's rounding: toFixed rounds halves up, Math.round rounds halves toward +infinity).
"""
import html
import math
from decimal import Decimal, ROUND_HALF_UP

MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
MONTH = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
         "November", "December"]
CLASS_WORD = {"signal": "signal", "edge": "at the edge", "noise": "noise"}


# ------------------------------------------------------------------ JavaScript-identical formatting
def fixed(v, n):
    """JavaScript's Number.prototype.toFixed(n)."""
    q = Decimal(1).scaleb(-n)
    return str(Decimal(float(v)).quantize(q, rounding=ROUND_HALF_UP))


def jsround(v):
    return math.floor(float(v) + 0.5)


def sign_fixed(v, n):
    v = float(v)
    return ("+" if v > 0 else "−" if v < 0 else "") + fixed(abs(v), n)


def fmt1(v): return sign_fixed(v, 1)
def fmt2(v): return sign_fixed(v, 2)


def num(v):
    """A whole number as JavaScript prints it in a template string (ints stay ints; 12.0 prints as 12)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def ordinal(n):
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def md_date(md):
    m, d = (int(x) for x in md.split("-"))
    return f"{MON[m - 1]} {d}"


def long_date(iso):
    y, m, d = (int(x) for x in iso.split("-"))
    return f"{MONTH[m - 1]} {d}, {y}"


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace('"', "&quot;")


def table(head, rows):
    return ("<table><thead><tr>" + "".join(f"<th>{h}</th>" for h in head) + "</tr></thead><tbody>"
            + "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows) + "</tbody></table>")


# ------------------------------------------------------------------ the blocks
def stat_line(r, kind, words):
    if not r["notable"]:
        return "typical for " + words
    w = "warmest" if r["standing"] == "warm" else ("coldest" if kind == "low" else "coolest")
    return (("record " + w) if r["record"] else (ordinal(r["rank"]) + " " + w)) + (" (tied)" if r["tied"] else "") + " of " + str(r["years"])


def blocks(D):
    """{element id: inner HTML} for every element the page script fills that should be in the HTML."""
    Y, S, R, G, P = D["yesterday"], D["season"], D["records"], D["change"], D["rain"]
    C, year, first = D["city"]["name"], D["year"], D["first_year"]
    out = {}

    stats = [
        ("Yesterday's high", f"{Y['hi']}°F", stat_line(Y["r_hi"], "high", Y["words"]), Y["r_hi"]["notable"]),
        ("Yesterday's low", f"{Y['lo']}°F", stat_line(Y["r_lo"], "low", Y["words"]), Y["r_lo"]["notable"]),
        ("Normal high", f"{jsround(Y['norm_hi'])}°F", "1991–2020 average for " + Y["words"], False),
        ("Record high", f"{Y['rec_hi']}°F", "set in " + ", ".join(str(y) for y in Y["rec_hi_years"]), False),
    ]
    out["stats"] = "".join(f'<div class="stat" role="listitem"><span class="lab">{l}</span><b class="{"sig" if sig else ""}">{v}</b><span>{s}</span></div>'
                           for l, v, s, sig in stats)

    # 1. Yesterday
    out["f-yest-title"] = esc(f"Every {Y['words']} high and low since {first}, °F")
    lo = {y: v for y, v in Y["lo_vals"]}
    out["tb-yest"] = table(["Year", "High °F", "Low °F"],
                           [[y, v, lo.get(y, "–")] for y, v in reversed(Y["hi_vals"])])

    # 2. The year so far
    def rec_list(days):
        return ", ".join(f"{md_date(r['md'])} ({r['v']}°F{', tied' if r['tie'] else ''})" for r in days)
    nRH, nRL = len(S["rec_hi_days"]), len(S["rec_lo_days"])
    diff = S["ytd_pr"] - S["norm_ytd_pr"]
    out["p-year"] = (
        f"{S['pct_above']}% of days in {year} so far have averaged warmer than normal. "
        + (f"{esc(C)} has set or tied <strong class=\"sig\">{nRH} daily record high{'' if nRH == 1 else 's'}</strong>: {rec_list(S['rec_hi_days'])}. " if nRH else "")
        + (f"It has also set or tied {nRL} daily record low{'' if nRL == 1 else 's'}: {rec_list(S['rec_lo_days'])}. " if nRL else "It hasn't set a single daily record low. ")
        + f"Precipitation so far: <strong>{fixed(S['ytd_pr'], 2)} inches</strong>, {fixed(abs(diff), 2)} {'above' if diff >= 0 else 'below'} "
          f"the 1991–2020 average to this date ({fixed(S['norm_ytd_pr'], 2)}).")
    out["f-year-title"] = esc(f"{C} in {year}: daily highs and lows against normal and record, °F")
    idx = {d: i for i, d in enumerate(S["days"])}
    rows = []
    for d, hi_, lo_, rh, rl in reversed(S["year"]):
        i = idx[d]
        rows.append([md_date(d), f"{'–' if hi_ is None else hi_}{' ★' if rh else ''}",
                     f"{'–' if lo_ is None else lo_}{' ★' if rl else ''}",
                     jsround(S["norm_hi"][i]), jsround(S["norm_lo"][i]), S["rec_hi"][i], S["rec_lo"][i]])
    out["tb-year"] = table(["Date", "High °F", "Low °F", "Normal high", "Normal low", "Record high", "Record low"], rows)

    # 3. Record book
    book = []
    for title, rws, lab in (("Hottest days", R["hot"], "High"), ("Coldest nights", R["cold"], "Low")):
        body = ""
        for i, r in enumerate(rws):
            prev = i and rws[i - 1]["v"] == r["v"]
            rank = next(k for k, q in enumerate(rws) if q["v"] == r["v"]) + 1
            body += f"<tr><td>{'' if prev else rank}</td><td>{long_date(r['date'])}</td><td>{r['v']}°F</td></tr>"
        book.append(f'<div><h3>{title} on record</h3><div class="tablewrap" style="max-height:none"><table><thead><tr>'
                    f'<th>#</th><th>Date</th><th>{lab}</th></tr></thead><tbody>{body}</tbody></table></div></div>')
    out["book"] = "".join(book)
    out["tb-rec"] = table(["Decade", "Years", "Record highs", "Expected", "Record lows", "Expected"],
                          [[f"{r['d']}s", r["yrs"], fixed(r["hi"], 1), fixed(r["ehi"], 1), fixed(r["lo"], 1), fixed(r["elo"], 1)]
                           for r in R["decades"]])

    # 4. How it has changed
    out["s-ann"] = (f"Average of the 12 monthly means, °F. {G['complete_years']} complete years; "
                    f"{len(G['missing_years'])} years with a month missing more than 9 days are left out.")
    alo = {y: v for y, v in G["ann_lo"]}
    out["tb-ann"] = table(["Year", "Avg high °F", "Avg low °F"],
                          [[y, fixed(v, 1), fixed(alo[y], 1) if y in alo else "–"] for y, v in reversed(G["ann_hi"])])
    out["f-mon-title"] = esc(D["text"]["t_mon"])
    out["tb-mon"] = table(["Month", "Highs °F/decade", "95% range", "Class", "Lows °F/decade", "95% range", "Class"],
                          [[MONTH[r["m"] - 1], fmt2(r["hi"]["per_decade"]), f"{fmt2(r['hi']['lo'])} to {fmt2(r['hi']['hi'])}",
                            CLASS_WORD[r["hi"]["class"]], fmt2(r["lo"]["per_decade"]), f"{fmt2(r['lo']['lo'])} to {fmt2(r['lo']['hi'])}",
                            CLASS_WORD[r["lo"]["class"]]] for r in G["months"]])
    decs = sorted({c["d"] for c in G["heat"]})
    heat = {(c["d"], c["m"]): c["a"] for c in G["heat"]}
    out["tb-heat"] = table(["Month"] + [f"{d}s" for d in decs],
                           [[mo] + [fmt1(heat[(d, j + 1)]) if (d, j + 1) in heat else "–" for d in decs]
                            for j, mo in enumerate(MONTH)])

    # 5. Rain and snow
    out["tb-pr"] = table(["Year", "Inches"], [[y, fixed(v, 2)] for y, v in reversed(P["ann"])])
    out["f-dry-title"] = (f"Dry spells of {P['dry_min']} days or more, every year since {first}" if P["longest"]
                          else f"No dry spell of {P['dry_min']} days or more since {first}")
    out["tb-dry"] = table(["Start", "Days without measurable rain"], [[long_date(d["start"]), d["n"]] for d in reversed(P["dry"])])
    if D["text"]["show_snow"]:
        out["s-snow"] = (f"Inches of snow, each winter with complete November–March records. "
                         f"The 1991–2020 average is {fixed(P['snow_normal'], 1)} inches.")
        out["tb-snow"] = table(["Winter", "Inches"], [[f"{p['s']}–{str(p['s'] + 1)[2:]}", fixed(p["v"], 1)] for p in reversed(P["snow"])])
    return out


def method_items(D):
    """The short "How this was measured" list kept on every page (the long explanations moved to the shared
    "How the Almanac works" page, Nov 24, 2026)."""
    c = D["city"]
    items = [
        f"<strong>The record:</strong> {esc(c['record_name'])}, {esc(c['record_kind'])}. Daily values from NOAA through "
        f"RCC-ACIS, {D['first_year']} to {long_date(D['asof'])}, in whole °F as recorded. Updated several times a day as stations "
        f"post and correct values.",
        f"<strong>Unadjusted readings.</strong> {esc(c['caveat'])}",
    ]
    return "".join(f"<li>{t}</li>" for t in items)


def fill(page, D):
    """Write every block into the page's empty elements (each must appear exactly once, empty)."""
    import re
    parts = blocks(D)
    parts["method"] = method_items(D)
    if not D["text"]["show_snow"]:   # the page script hides the snowfall chart for places with little snow
        page = page.replace('id="fig-snow"', 'id="fig-snow" hidden', 1)
    for el_id, inner in parts.items():
        pat = re.compile(rf'(id="{re.escape(el_id)}"[^>]*>)(</)')
        page, n = pat.subn(lambda m: m.group(1) + inner + m.group(2), page, count=1)
        if n != 1:
            raise ValueError(f"prerender: no empty element with id {el_id!r} in the template")
    return page
