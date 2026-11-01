"""
Add schemes the site's universe has gained since the one-time harvest.

The nightly only appends to schemes already in the store, so a scheme that
joins the site's universe (amfi_scheme_master, rebuilt monthly) never gets
NAVs here. Found 1 Nov 2026: 223 schemes missing, among them every Motilal
Oswal index fund. Backtest hid it by falling back to mfapi per request; the
fund-page and X-Ray risk figures, which read only the store, could not.

This harvests full history for just the missing schemes from api.mfapi.in,
with the same fetch as harvest.py, and republishes the store. Run it monthly
right after the site's build_scheme_master.py (docs/MONTHLY_UPDATE.md), from
a residential machine like the original harvest; never from the nightly.

    python scripts/topup_missing.py --db <path to mfoverlap_cache.db> [--dry-run]
"""
import argparse
import os
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harvest
import store

REPO = os.environ.get("GITHUB_REPOSITORY", store.REPO_DEFAULT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=harvest.DB)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    universe = {str(r[0]) for r in db.execute("SELECT scheme_code FROM amfi_scheme_master")}
    db.close()

    work, blob = store.download(REPO)
    schemes = blob["schemes"]
    missing = sorted(universe - set(schemes), key=int)
    print(f"universe {len(universe)}, store {len(schemes)}, missing {len(missing)}")
    if not missing or args.dry_run:
        return

    def one(code):
        result = harvest.fetch(code)
        time.sleep(0.15)                      # politeness, as in harvest.py
        return result

    added, failed = 0, {}
    with ThreadPoolExecutor(max_workers=harvest.WORKERS) as pool:
        for code, series, err in pool.map(one, missing):
            if series:
                schemes[str(code)] = series
                added += 1
            else:
                failed[code] = err
    for c, e in sorted(failed.items(), key=lambda t: int(t[0])):
        print(f"  not added {c}: {e}")
    if not added:
        raise SystemExit("nothing fetched; store left as it was")
    as_of = store.publish(work, blob, REPO, topped_up=added)
    print(f"added {added} schemes ({len(failed)} not available); store now "
          f"{len(schemes)} schemes, as_of {as_of}")


if __name__ == "__main__":
    main()
