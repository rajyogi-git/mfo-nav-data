"""
Nightly append: AMFI's official NAVAll.txt -> the consolidated store asset.

Runs in GitHub Actions on a schedule. Fetches AMFI's daily file (every scheme
in India, ~1.5MB, official and free), appends any date newer than each scheme's
last row, and re-uploads the store plus a small manifest.

mfapi is never touched here. The store's scheme universe is fixed by the
backfill; schemes that later join the site's universe are served by the app's
mfapi fallback until the next backfill refresh.

Exit codes: 0 = updated, 0 + "no-op" = AMFI's file had nothing newer than the
store (weekend, holiday), 1 = hard failure - unreachable feed, or a file we
could not read (the workflow turns that into a visible red run).

On that last point: until 2026-08-23 an unreadable file was NOT a hard failure.
AMFI inserted two columns, every row stopped parsing, and the empty result fell
through to the "no-op" branch - so five days of stale NAVs sat behind five
green runs. Parsing now happens before anything else and a short read is fatal.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import navfeed
import store

REPO   = os.environ.get("GITHUB_REPOSITORY", store.REPO_DEFAULT)
NAVALL = "https://www.amfiindia.com/spages/NAVAll.txt"

# Sanity floor: AMFI's daily file carries every scheme in India - ~14,000
# parseable rows, of which the store's universe is a subset. Anything far below
# that is a format change or a truncated download, not a quiet market day.
MIN_SCHEMES = 5000


def fetch_navall():
    return navfeed.fetch_text(NAVALL, "AMFI NAVAll.txt")


def parse_navall(txt):
    """{code: (iso_date, nav)} for every parseable row.

    Field positions come from the file's own header via navfeed, never from
    hardcoded indices - see that module's docstring for the incident that made
    this the rule.
    """
    return navfeed.latest_by_scheme(txt)


def main():
    # AMFI first, the store second. The daily file is 1.5MB and the store is
    # 38MB, so a format change fails in seconds instead of after a large
    # download we were never going to use.
    daily = parse_navall(fetch_navall())
    if len(daily) < MIN_SCHEMES:
        raise SystemExit(
            f"parsed only {len(daily)} schemes from NAVAll.txt, expected at "
            f"least {MIN_SCHEMES} - the feed's format or content has changed. "
            f"Failing loudly rather than reporting a no-op; the parser is "
            f"scripts/navfeed.py.")

    work, blob = store.download(REPO)
    schemes = blob["schemes"]

    appended = skipped = 0
    for code, series in schemes.items():
        row = daily.get(code)
        if not row:
            continue
        d, nav = row
        if series and series[-1][0] >= d:
            skipped += 1
            continue
        series.append([d, nav])
        appended += 1

    if appended == 0:
        # Genuinely nothing new. Distinguishable from the failure above because
        # we know the file parsed: say so, so the log proves which case it was.
        print(f"no-op: read {len(daily)} schemes from AMFI, none newer than "
              f"the store's {len(schemes)} (weekend/holiday?)")
        return

    as_of = store.publish(work, blob, REPO, appended=appended)
    print(f"appended {appended} schemes ({skipped} already current); "
          f"as_of now {as_of}")


if __name__ == "__main__":
    main()
