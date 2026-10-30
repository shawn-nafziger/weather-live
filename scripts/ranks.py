"""
ranks.py

Same-date ranks against a city's official record (history/<city>.csv).

The rules (same as weather-intelligence/knowledge/reference/methods.md):
- Compare whole °F, the units the official record is kept in.
- Rank = years strictly warmer + 1, so ties share a rank. Never a dense rank.
- Always report how many years the rank is out of and when the record starts.
- "Warmest since 1999" means 1999 is the most recent earlier year that was at
  least as warm.
- Mid-pack days get plain wording ("typical for October 29"), not "60th coolest".
"""

import csv
import math
from datetime import date
from pathlib import Path

# A value is described by its rank only when it falls in the warmest or coolest
# fifth of the record (at least the top/bottom 3, for short records). Otherwise
# it's "typical". An editorial choice, set here in one place.
NOTABLE_SHARE = 0.20
NOTABLE_MIN_RANK = 3


def whole_f(value_f):
    """Round half up to whole °F (Python's round() rounds 70.5 down to 70)."""
    return None if value_f is None else int(math.floor(float(value_f) + 0.5))


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def load_history(path):
    """{date: row} from history/<city>.csv, or {} if the file doesn't exist yet."""
    path = Path(path)
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {date.fromisoformat(r["Date"]): r for r in csv.DictReader(f)}


def same_date_values(history, day, field):
    """[(year, whole °F)] for every year on this calendar date, excluding `day` itself."""
    out = []
    for d, row in history.items():
        if d.month == day.month and d.day == day.day and d != day and row.get(field) not in ("", None):
            out.append((d.year, whole_f(row[field])))
    return out


def rank(history, day, value_f, field="MaxTempF"):
    """How `value_f` on `day` ranks among every same calendar date in the record.

    If `day` is already in the record (yesterday's final value), it is compared
    with the other years. If it isn't (today so far, a forecast), the result is
    what it *would* rank, counting this year in the total.
    """
    if value_f is None:
        return None
    others = same_date_values(history, day, field)
    if not others:
        return None
    v = whole_f(value_f)
    years = len(others) + 1
    warm = sum(1 for _, x in others if x > v) + 1
    cool = sum(1 for _, x in others if x < v) + 1
    tied = sorted(y for y, x in others if x == v)
    earlier = [(y, x) for y, x in others if y < day.year]
    warm_since = max((y for y, x in earlier if x >= v), default=None)
    cool_since = max((y for y, x in earlier if x <= v), default=None)
    first_year = min(min(y for y, _ in others), day.year)
    warm_side = warm <= cool
    standing, n, since = ("warm", warm, warm_since) if warm_side else ("cool", cool, cool_since)
    return {
        "value_f": v, "warm_rank": warm, "cool_rank": cool, "years": years,
        "first_year": first_year, "tied_years": tied,
        "standing": standing, "notable": n <= max(NOTABLE_MIN_RANK, round(years * NOTABLE_SHARE)),
        "since": since,
        "record": n == 1,
    }


def phrase(r, day, kind="high"):
    """Plain wording, e.g. '4th warmest October 28 high of 155 since 1872, warmest since 1999'."""
    if r is None:
        return None
    date_words = f"{day:%B} {day.day}"
    if not r["notable"]:
        return f"typical for {date_words}"
    word = {("high", "warm"): "warmest", ("high", "cool"): "coolest",
            ("low", "warm"): "warmest", ("low", "cool"): "coldest"}[(kind, r["standing"])]
    n = r["warm_rank"] if r["standing"] == "warm" else r["cool_rank"]
    if r["record"]:
        verb = "ties the record for" if r["tied_years"] else "the"
        return f"{verb} {word} {date_words} {kind} on record ({r['years']} years since {r['first_year']})"
    tie = " (tied)" if r["tied_years"] else ""
    text = f"{ordinal(n)} {word} {date_words} {kind}{tie} of {r['years']} since {r['first_year']}"
    if r["since"]:
        text += f", {word} since {r['since']}"
    return text
