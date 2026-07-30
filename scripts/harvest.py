"""
One-time backfill: full NAV history for every scheme in the site's universe,
harvested from api.mfapi.in into a single consolidated store file.

This runs ONCE, from a residential machine — a one-off harvest is not a
traffic pattern. Nightly freshness afterwards comes from AMFI's official
NAVAll.txt via nightly.py; mfapi is never called on a schedule again.

Output: build/nav_store.json.gz
  {"built_utc": ..., "as_of": <newest NAV date anywhere>,
   "schemes": {"<code>": [["YYYY-MM-DD", nav], ...ascending...]}}
"""
import gzip, json, sqlite3, sys, time, threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import urllib.request

DB   = r"H:\My Drive\Laptop\2026\Investment\mutualfundoverlap.in\git\mfoverlap-production\mfoverlap_cache.db"
OUT  = Path(__file__).resolve().parent.parent / "build" / "nav_store.json.gz"
LOG  = OUT.with_name("harvest.log")
URL  = "https://api.mfapi.in/mf/{code}"
UA   = "mutualfundoverlap.in one-time backfill (+https://mutualfundoverlap.in)"
WORKERS, RETRIES, TIMEOUT = 4, 3, 25

def log(msg):
    line = f"{datetime.now():%H:%M:%S} {msg}"
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def parse(payload, code):
    rows = payload.get("data") or []
    out = []
    for r in rows:
        try:
            d = datetime.strptime((r.get("date") or "").strip(), "%d-%m-%Y").date()
            nav = float((r.get("nav") or "").strip())
        except (ValueError, TypeError):
            continue
        if nav > 0:
            out.append([d.isoformat(), nav])
    out.sort(key=lambda r: r[0])
    return out

def fetch(code):
    for attempt in range(1, RETRIES + 1):
        try:
            req = urllib.request.Request(URL.format(code=code), headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                series = parse(json.loads(r.read().decode("utf-8", "replace")), code)
            if series:
                return code, series, None
            return code, None, "empty series"
        except Exception as exc:
            if attempt == RETRIES:
                return code, None, f"{type(exc).__name__}: {exc}"
            time.sleep(2 * attempt)
    return code, None, "unreachable"

def main():
    db = sqlite3.connect(DB)
    codes = sorted(r[0] for r in db.execute("SELECT scheme_code FROM amfi_scheme_master"))
    db.close()
    log(f"harvest start: {len(codes)} schemes, {WORKERS} workers")

    store, failures, done = {}, {}, [0]
    lock = threading.Lock()
    t0 = time.time()

    def one(code):
        code, series, err = fetch(code)
        with lock:
            done[0] += 1
            if series:
                store[str(code)] = series
            else:
                failures[code] = err
            if done[0] % 200 == 0:
                rate = done[0] / (time.time() - t0)
                log(f"  {done[0]}/{len(codes)} ({rate:.1f}/s, {len(failures)} failed)")
        time.sleep(0.15)   # politeness on top of the worker cap

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(one, codes))

    as_of = max((s[-1][0] for s in store.values()), default=None)
    blob = {"built_utc": datetime.now(timezone.utc).isoformat(),
            "as_of": as_of, "schemes": store}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(blob, f, separators=(",", ":"))

    log(f"DONE in {(time.time()-t0)/60:.1f} min: {len(store)} schemes stored, "
        f"{len(failures)} failed, as_of={as_of}, "
        f"file={OUT.stat().st_size/1e6:.1f}MB")
    for c, e in sorted(failures.items()):
        log(f"  FAILED {c}: {e}")

if __name__ == "__main__":
    main()
