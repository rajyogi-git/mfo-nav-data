# mfo-nav-data

NAV store for [mutualfundoverlap.in](https://mutualfundoverlap.in)'s Backtest
tool. **The data lives in the `nav-store` release assets, not in git** — one
consolidated `nav_store.json.gz` (full NAV history for every scheme in the
site's universe) plus a small `manifest.json` (newest date, scheme count).
Keeping data out of git history is deliberate: a nightly append rewrites the
whole store, and committing that nightly would grow the repo without bound.

- **Source of truth:** AMFI's official published NAV reports.
  `scripts/nightly.py` appends each day's `NAVAll.txt` (every scheme in
  India, ~1.6MB) on a 02:30 IST schedule.
- **Backfill:** `scripts/harvest.py`, run once from a residential machine.
- **Consumer:** the Flask app downloads the store on first use and serves
  every backtest from its own copy — no third-party API in the user path.
  api.mfapi.in remains only as the app's rare fallback for schemes missing
  from the store.

A red run of the nightly workflow means the store is going stale — the app
also surfaces staleness on `/health`, so a silent failure shows up in two
places.
