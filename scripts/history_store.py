"""
history_store.py

Where each place's official daily record lives on disk, split by decade:

    history/<slug>/1870s.csv, history/<slug>/1880s.csv, ..., history/<slug>/2020s.csv

Why: the daily job re-reads the last 21 days for every place. With one file per
place, every run rewrote the whole record (~2.5 MB) for every place; at 300 places
the repository would balloon. Split by decade, a normal run only changes each
place's current-decade file, and decade files whose content didn't change are not
rewritten at all.

Older single-file records (history/<slug>.csv) are still read, and are converted
to decade files the next time the daily job writes that place.

Used by station_update.py (writes), ranks.py / nws_pull.py (read) and
almanac/build.py (read).
"""

import csv
import io
from pathlib import Path

COLUMNS = ["Date", "MaxTempF", "MinTempF", "PrecipIn", "SnowIn", "PrecipTrace", "SnowTrace",
           "MaxTempC", "MinTempC", "PrecipMM", "SnowCM"]


def _legacy(base, slug):
    return Path(base) / f"{slug}.csv"


def _folder(base, slug):
    return Path(base) / slug


def files(base, slug):
    """The CSV files holding this place's record, oldest first (decade files, or the old single file)."""
    folder = _folder(base, slug)
    if folder.is_dir():
        found = sorted(folder.glob("*s.csv"))
        if found:
            return found
    legacy = _legacy(base, slug)
    return [legacy] if legacy.exists() else []


def exists(base, slug):
    return bool(files(base, slug))


def read_rows(base, slug):
    """Every day of the record as a list of dicts (strings, as stored), oldest first."""
    rows = []
    for path in files(base, slug):
        with path.open(newline="", encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


def _decade(date_text):
    return f"{int(date_text[:4]) // 10 * 10}s"


def _csv_text(rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def write_rows(base, slug, rows):
    """Save the record as decade files. Only files whose content changed are written.
    Returns the number of decade files written."""
    by_decade = {}
    for r in sorted(rows, key=lambda r: r["Date"]):
        by_decade.setdefault(_decade(r["Date"]), []).append(r)   # a bad date stops here, before anything is written
    folder = _folder(base, slug)
    folder.mkdir(parents=True, exist_ok=True)
    written = 0
    for decade, part in by_decade.items():
        path = folder / f"{decade}.csv"
        text = _csv_text(part)
        # open(..., newline="") rather than Path.read_text(newline=...), which needs Python 3.13
        # (GitHub's runner uses 3.12; this broke the Nov 3 runs).
        if path.exists():
            with path.open(encoding="utf-8", newline="") as f:
                if f.read() == text:
                    continue
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8", newline="") as f:
            f.write(text)
        tmp.replace(path)
        written += 1
    # A decade file with no rows left (shouldn't happen: days are never dropped) is kept, not deleted.
    legacy = _legacy(base, slug)
    if legacy.exists():
        legacy.unlink()          # converted: the decade files now hold everything
    return written
