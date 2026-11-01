"""
tri.py — Nifty Total Returns Index (TRI) history, topped up nightly into the
store's "tri" section: {"Nifty 50": [["YYYY-MM-DD", value], ...], ...}.

The site measures Backtest against the Nifty 50 TRI and fund-page / X-Ray
beta and alpha against each category's Nifty TRI (owner decision, 31 Oct and
1 Nov 2026). Shipping the TRIs with the NAVs keeps one freshness clock: the
site never compares today's NAVs with a month-old index.

Source: niftyindices.com's historical-data page (NSE Indices), backed by an
undocumented JSON endpoint (found 31 Oct 2026):
  POST /BackPage/getTotalReturnIndexString
  {"cinfo": "{'name':'NIFTY 500','startDate':'01-Jan-2026','endDate':'31-Oct-2026','indexName':'NIFTY 500'}"}
  -> [{"Index Name", "Date": "30 Oct 2026", "TotalReturnsIndex": "35239.19", ...}]
The page allows at most one year per request, so a first run (no "tri"
section yet) asks year by year from START_DATE; later runs ask only for the
days since each series' last row.

This step must never cost the NAV append: nightly.py calls top_up() inside a
try and publishes the NAVs whatever happens here.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, datetime, timedelta
from http.cookiejar import CookieJar

INDICES = ["Nifty 50", "Nifty 100", "Nifty Midcap 150", "Nifty Smallcap 250",
           "Nifty LargeMidcap 250", "Nifty 500"]
START_DATE = date(2013, 1, 1)      # Backtest keeps 13 years of NAVs
PAGE = "https://www.niftyindices.com/reports/historical-data"
API  = "https://www.niftyindices.com/BackPage/getTotalReturnIndexString"
PAUSE_S = 1.0
_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "Origin": "https://www.niftyindices.com", "Referer": PAGE}


def parse(text: str) -> list[list]:
    """The endpoint's JSON -> ascending [[iso, value]]. Rows without a number
    ('-', blank) are skipped."""
    out = {}
    for row in json.loads(text or "[]"):
        try:
            iso = datetime.strptime(row["Date"].strip(), "%d %b %Y").date().isoformat()
            out[iso] = float(str(row["TotalReturnsIndex"]).replace(",", ""))
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
    return [[d, out[d]] for d in sorted(out)]


def _opener():
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    op.open(urllib.request.Request(PAGE, headers={"User-Agent": "Mozilla/5.0"}), timeout=60).read()
    return op


def _fetch(op, index_name: str, start: date, end: date) -> list[list]:
    name = index_name.upper()
    rows: list[list] = []
    while start <= end:
        stop = min(end, start + timedelta(days=364))
        cinfo = (f"{{'name':'{name}','startDate':'{start:%d-%b-%Y}',"
                 f"'endDate':'{stop:%d-%b-%Y}','indexName':'{name}'}}")
        req = urllib.request.Request(API, data=json.dumps({"cinfo": cinfo}).encode(),
                                     headers=_HEADERS, method="POST")
        rows.extend(parse(op.open(req, timeout=60).read().decode("utf-8")))
        start = stop + timedelta(days=1)
        time.sleep(PAUSE_S)
    return rows


def merge(series: list, rows: list) -> int:
    """Append rows newer than series' last date (in place); returns how many."""
    last = series[-1][0] if series else ""
    new = [r for r in rows if r[0] > last]
    series.extend(new)
    return len(new)


def top_up(blob: dict, today: date | None = None) -> int:
    """Add the days since each index's last row to blob["tri"]. Returns the
    number of rows added. Raises on a network or format failure; the caller
    decides what that costs (nothing, for the NAV append)."""
    today = today or date.today()
    tri = blob.setdefault("tri", {})
    op = _opener()
    added = 0
    for name in INDICES:
        series = tri.setdefault(name, [])
        start = (date.fromisoformat(series[-1][0]) + timedelta(days=1)) if series else START_DATE
        if start > today:
            continue
        added += merge(series, _fetch(op, name, start, today))
    short = [n for n in INDICES if len(tri.get(n) or []) < 1000]
    if short:
        raise RuntimeError(f"TRI history too short for {short}; the endpoint may have changed")
    return added
