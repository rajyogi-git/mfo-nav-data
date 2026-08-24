"""
Fill a date range in the store from AMFI's NAV history report.

Why this exists: NAVAll.txt only ever carries one day. When the nightly stops
landing — as it did 2026-08-19..20, when a column insertion broke the parser —
those days are gone for good once the feed moves on, and the nightly can only
ever append to the END of a series. This script inserts interior dates.

    python scripts/backfill_range.py --from 2026-08-19 --to 2026-08-20

The history report is a different endpoint with a DIFFERENT column order to
NAVAll.txt (scheme name is field 1, not field 3), which is exactly why navfeed
resolves columns by header name instead of by position.

Coverage is partial by nature: the history report carries fewer schemes than
the daily file (~8,600 vs ~14,000). Schemes it omits keep their gap, and the
run prints how many rather than quietly implying a complete fill.
"""
import argparse
import os
import sys
from datetime import date, datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import navfeed
import store

REPO = os.environ.get("GITHUB_REPOSITORY", store.REPO_DEFAULT)
HISTORY_URL = ("https://portal.amfiindia.com/DownloadNAVHistoryReport_Po.aspx"
               "?frmdt={frm}&todt={to}")


def _amfi_date(iso: str) -> str:
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%d-%b-%Y")


def merge(series: list, rows: list) -> int:
    """Insert [date, nav] rows into an ascending series, returning how many
    landed. Existing dates win — a backfill fills holes, it never overwrites a
    NAV the store already has."""
    have = {d for d, _ in series}
    new = [r for r in rows if r[0] not in have]
    if not new:
        return 0
    series.extend(new)
    series.sort(key=lambda r: r[0])
    return len(new)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", required=True, help="YYYY-MM-DD")
    ap.add_argument("--to", dest="to", required=True, help="YYYY-MM-DD")
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would change; touch no release asset")
    a = ap.parse_args()

    frm, to = date.fromisoformat(a.frm), date.fromisoformat(a.to)
    if frm > to:
        raise SystemExit(f"--from {frm} is after --to {to}")

    url = HISTORY_URL.format(frm=_amfi_date(a.frm), to=_amfi_date(a.to))
    print(f"-> {url}")
    feed = navfeed.series_by_scheme(
        navfeed.fetch_text(url, "AMFI history report"))
    if not feed:
        raise SystemExit("history report parsed to zero rows - format change, "
                         "or the range predates AMFI's retention window")

    dates = sorted({d for rows in feed.values() for d, _ in rows})
    print(f"   {len(feed)} schemes, {sum(len(v) for v in feed.values())} rows, "
          f"dates {dates[0]}..{dates[-1]}")
    if a.dry_run:
        print("dry run - stopping before the store")
        return

    work, blob = store.download(REPO)
    schemes = blob["schemes"]
    before = store.compute_as_of(schemes)

    filled = touched = 0
    for code, series in schemes.items():
        rows = feed.get(code)
        if not rows:
            continue
        n = merge(series, rows)
        if n:
            filled += n
            touched += 1

    missing = sum(1 for c in schemes if c not in feed)
    print(f"   inserted {filled} rows across {touched} schemes; "
          f"{missing} of {len(schemes)} absent from the report (gap kept)")
    if filled == 0:
        print("no-op: every scheme already had these dates")
        return

    as_of = store.publish(work, blob, REPO, backfilled=filled,
                          backfill_range=f"{a.frm}..{a.to}")
    # A backfill inserts interior dates, so the newest row per scheme — and
    # therefore as_of — must not move. If it did, something appended instead.
    if as_of != before:
        print(f"   NOTE: as_of moved {before} -> {as_of}")
    else:
        print(f"   as_of unchanged at {as_of}, as expected for interior dates")


if __name__ == "__main__":
    main()
