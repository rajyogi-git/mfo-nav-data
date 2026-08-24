"""
store.py — download, date-stamp and re-publish the consolidated NAV store.

Shared by nightly.py (appends the newest day) and backfill_range.py (fills a
gap from AMFI's history report). The `as_of` rule in particular must have
exactly one implementation: it is subtle, it feeds the site's user-visible
"NAVs updated" label, and it has been got wrong once already — on 2026-08-01 a
plain max() picked up AMFI's forward-dated weekend liquid NAVs and stamped the
store with a date in the future.
"""
from __future__ import annotations

import gzip
import json
import subprocess
import tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_DEFAULT = "rajyogi-git/mfo-nav-data"
TAG   = "nav-store"
STORE = "nav_store.json.gz"
MANI  = "manifest.json"

IST = timezone(timedelta(hours=5, minutes=30))


def sh(*args) -> None:
    subprocess.run(args, check=True)


def download(repo: str) -> tuple[Path, dict]:
    """(workdir, blob) — the current store from the release asset."""
    work = Path(tempfile.mkdtemp())
    sh("gh", "release", "download", TAG, "--repo", repo,
       "--pattern", STORE, "--dir", str(work))
    with gzip.open(work / STORE, "rt", encoding="utf-8") as f:
        return work, json.load(f)


def compute_as_of(schemes: dict) -> str:
    """The date the universe is genuinely current to — not a plain max().

    AMFI forward-dates overnight/liquid NAVs: a Friday file carries Saturday
    and Sunday rows for a small minority of schemes. Rule: the newest
    non-future (IST) date that at least 1% of schemes have reached. The
    forward-dated rows stay in the store and serve fine; they just don't get
    to name the store's freshness.
    """
    today_ist = datetime.now(timezone.utc).astimezone(IST).date().isoformat()
    tally = Counter(s[-1][0] for s in schemes.values() if s)
    if not tally:
        raise SystemExit("store has no dated rows — refusing to stamp an as_of")
    eligible = sorted((d for d in tally if d <= today_ist), reverse=True)
    threshold = max(1, len(schemes) // 100)
    as_of, cum = (eligible[-1] if eligible else max(tally)), 0
    for d in eligible:
        cum += tally[d]
        if cum >= threshold:
            return d
    return as_of


def publish(work: Path, blob: dict, repo: str, **manifest_extra) -> str:
    """Re-stamp as_of/built_utc, write both assets, upload. Returns as_of."""
    schemes = blob["schemes"]
    blob["as_of"] = compute_as_of(schemes)
    blob["built_utc"] = datetime.now(timezone.utc).isoformat()
    with gzip.open(work / STORE, "wt", encoding="utf-8") as f:
        json.dump(blob, f, separators=(",", ":"))
    (work / MANI).write_text(json.dumps({
        "as_of": blob["as_of"], "schemes": len(schemes),
        "updated_utc": blob["built_utc"], **manifest_extra,
    }, indent=1), encoding="utf-8")
    sh("gh", "release", "upload", TAG, str(work / STORE), str(work / MANI),
       "--repo", repo, "--clobber")
    return blob["as_of"]
