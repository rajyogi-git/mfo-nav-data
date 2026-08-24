"""
navfeed.py — the one place that fetches and understands AMFI's NAV feeds.

Why this module exists (2026-08-23 incident): AMFI inserted two columns —
`Plan` and `Option` — into NAVAll.txt, moving Net Asset Value from field 4 to
field 6 and Date from 5 to 7. `nightly.py` read those fields by hardcoded
index, so `float("Direct Plan")` raised on every single row, every row was
skipped by the per-row `except: continue`, and the run reported the empty
result as a benign weekend "no-op". The store sat at 2026-08-18 for five days
behind five green Action runs; only /health's staleness check noticed.

So: columns are located by HEADER NAME, never by position. AMFI publishes two
feeds and they do not agree on order —

  NAVAll.txt       Scheme Code;ISIN1;ISIN2;Scheme Name;Plan;Option;Net Asset Value;Date
  NAVHistoryReport Scheme Code;NAV Name;Plan;Option;ISIN1;ISIN2;Net Asset Value;Date

— the scheme name is field 3 in one and field 1 in the other. A parser that
handles both by name is the same amount of code as one that handles either by
index, and it survives the next insertion without a red run.

Header rows repeat throughout both files (once per section). Each one re-latches
the layout, so a mid-file change of shape is handled too.
"""
from __future__ import annotations

import time
import urllib.request
from datetime import datetime

UA = {"User-Agent": "mfo-nav-data (+https://mutualfundoverlap.in)"}


def fetch_text(url: str, what: str = "AMFI feed", retries: int = 4) -> str:
    """GET with a backing-off retry. AMFI times out from GitHub's runners often
    enough to matter (a red run on 2026-08-21 was exactly this and nothing
    more), so transient failure must not be confused with a format change."""
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:                                # noqa: BLE001
            last = exc
            if attempt < retries:
                time.sleep(10 * attempt)
    raise SystemExit(f"{what} unreachable after {retries} tries: {last}")

# Header labels -> the field we want. Matched on the exact lowercased cell text.
_CODE   = ("scheme code",)
_NAV    = ("net asset value",)
_DATE   = ("date",)
_NAME   = ("scheme name", "nav name")
_PLAN   = ("plan",)
_OPTION = ("option",)

_DATE_FMT = "%d-%b-%Y"          # AMFI: "21-Aug-2026"


class Layout:
    """Field positions for the header row currently in force."""
    __slots__ = ("code", "nav", "date", "name", "plan", "option")

    def __init__(self, cells: list[str]):
        def find(labels):
            for i, c in enumerate(cells):
                if c in labels:
                    return i
            return None
        self.code   = find(_CODE)
        self.nav    = find(_NAV)
        self.date   = find(_DATE)
        self.name   = find(_NAME)
        self.plan   = find(_PLAN)
        self.option = find(_OPTION)

    @property
    def usable(self) -> bool:
        return None not in (self.code, self.nav, self.date)

    def get(self, parts: list[str], idx: int | None) -> str:
        if idx is None or idx >= len(parts):
            return ""
        return parts[idx]


def _is_header(cells: list[str]) -> bool:
    return "scheme code" in cells and "net asset value" in cells


def iter_rows(text: str):
    """Yield {code, name, plan, option, nav, date} for every parseable data row.

    `nav` is a float > 0 and `date` an ISO string; rows that fail either
    conversion (AMFI writes "N.A." for suspended schemes) are skipped, as are
    rows appearing before any header. Section banners and AMC names have no
    semicolons and fall out naturally.
    """
    layout: Layout | None = None
    for line in text.splitlines():
        if ";" not in line:
            continue
        parts = [p.strip() for p in line.rstrip("\r\n").split(";")]
        cells = [p.lower() for p in parts]

        if _is_header(cells):
            candidate = Layout(cells)
            layout = candidate if candidate.usable else layout
            continue
        if layout is None:
            continue

        code = layout.get(parts, layout.code)
        if not code.isdigit():
            continue
        try:
            nav = float(layout.get(parts, layout.nav))
            date = datetime.strptime(
                layout.get(parts, layout.date), _DATE_FMT).date().isoformat()
        except (ValueError, TypeError):
            continue
        if nav <= 0:
            continue

        yield {
            "code":   code,
            "name":   layout.get(parts, layout.name),
            "plan":   layout.get(parts, layout.plan),
            "option": layout.get(parts, layout.option),
            "nav":    nav,
            "date":   date,
        }


def latest_by_scheme(text: str) -> dict[str, tuple[str, float]]:
    """{code: (iso_date, nav)} keeping the newest date seen per scheme.

    NAVAll.txt carries one day, so this is a straight read; the history report
    carries a range, and taking the max keeps that caller honest too.
    """
    out: dict[str, tuple[str, float]] = {}
    for r in iter_rows(text):
        prev = out.get(r["code"])
        if prev is None or r["date"] > prev[0]:
            out[r["code"]] = (r["date"], r["nav"])
    return out


def series_by_scheme(text: str) -> dict[str, list[list]]:
    """{code: [[iso_date, nav], ...ascending...]} — every row, deduped by date.

    For range backfills, where one file legitimately carries many dates per
    scheme and all of them are wanted.
    """
    seen: dict[str, dict[str, float]] = {}
    for r in iter_rows(text):
        seen.setdefault(r["code"], {})[r["date"]] = r["nav"]
    return {code: [[d, navs[d]] for d in sorted(navs)]
            for code, navs in seen.items()}
