"""
Nightly append: AMFI's official NAVAll.txt -> the consolidated store asset.

Runs in GitHub Actions on a schedule. Downloads the current store from the
repo's `nav-store` release, fetches AMFI's daily file (every scheme in India,
~1.6MB, official and free), appends any date newer than each scheme's last
row, and re-uploads the store plus a small manifest.

mfapi is never touched here. The store's scheme universe is fixed by the
backfill; schemes that later join the site's universe are served by the app's
mfapi fallback until the next backfill refresh.

Exit codes: 0 = updated, 0 + "no-op" = AMFI file had nothing new (weekend,
holiday), 1 = hard failure (the workflow turns that into a visible red run).
"""
import gzip, json, os, subprocess, sys, tempfile, time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
import urllib.request

REPO   = os.environ.get("GITHUB_REPOSITORY", "rajyogi-git/mfo-nav-data")
TAG    = "nav-store"
STORE  = "nav_store.json.gz"
MANI   = "manifest.json"
NAVALL = "https://www.amfiindia.com/spages/NAVAll.txt"
UA     = {"User-Agent": "mfo-nav-data nightly (+https://mutualfundoverlap.in)"}

def fetch_navall(retries=4):
    last = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(NAVALL, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as exc:
            last = exc
            time.sleep(10 * attempt)
    raise SystemExit(f"AMFI NAVAll.txt unreachable after {retries} tries: {last}")

def parse_navall(txt):
    """{code: (iso_date, nav)} for every parseable row."""
    out = {}
    for line in txt.splitlines():
        parts = line.split(";")
        if len(parts) < 6 or not parts[0].strip().isdigit():
            continue
        code, raw_nav, raw_date = parts[0].strip(), parts[4].strip(), parts[5].strip()
        try:
            nav = float(raw_nav)
            d   = datetime.strptime(raw_date, "%d-%b-%Y").date().isoformat()
        except (ValueError, TypeError):
            continue
        if nav > 0:
            out[code] = (d, nav)
    return out

def sh(*args):
    subprocess.run(args, check=True)

def main():
    work = Path(tempfile.mkdtemp())
    sh("gh", "release", "download", TAG, "--repo", REPO,
       "--pattern", STORE, "--dir", str(work))
    with gzip.open(work / STORE, "rt", encoding="utf-8") as f:
        blob = json.load(f)
    schemes = blob["schemes"]

    daily = parse_navall(fetch_navall())
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
        print(f"no-op: nothing newer than the store for any of "
              f"{len(schemes)} schemes (weekend/holiday?)")
        return

    # as_of feeds the site's "NAVs updated:" label, so it must be the date the
    # universe is genuinely current to — not a plain max(). AMFI forward-dates
    # overnight/liquid NAVs (Friday's file carried Sat+Sun rows for 18 of
    # 3,577 schemes, stamping the old max() "2026-08-02"). Rule: the newest
    # non-future (IST) date that at least 1% of schemes have reached. The
    # forward-dated rows themselves stay in the store and serve fine.
    today_ist = datetime.now(timezone.utc).astimezone(
        timezone(timedelta(hours=5, minutes=30))).date().isoformat()
    tally = Counter(s[-1][0] for s in schemes.values() if s)
    eligible = sorted((d for d in tally if d <= today_ist), reverse=True)
    threshold = max(1, len(schemes) // 100)
    as_of, cum = eligible[-1] if eligible else max(tally), 0
    for d in eligible:
        cum += tally[d]
        if cum >= threshold:
            as_of = d
            break
    blob["as_of"] = as_of
    blob["built_utc"] = datetime.now(timezone.utc).isoformat()
    with gzip.open(work / STORE, "wt", encoding="utf-8") as f:
        json.dump(blob, f, separators=(",", ":"))
    (work / MANI).write_text(json.dumps({
        "as_of": blob["as_of"], "schemes": len(schemes),
        "updated_utc": blob["built_utc"], "appended": appended,
    }, indent=1), encoding="utf-8")

    sh("gh", "release", "upload", TAG, str(work / STORE), str(work / MANI),
       "--repo", REPO, "--clobber")
    print(f"appended {appended} schemes ({skipped} already current); "
          f"as_of now {blob['as_of']}")

if __name__ == "__main__":
    main()
