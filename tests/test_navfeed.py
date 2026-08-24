"""
Parser tests for scripts/navfeed.py.

The repo had no tests when AMFI inserted `Plan` and `Option` into NAVAll.txt on
~2026-08-19, so a parser that returned zero rows shipped for five days behind
green Action runs. The load-bearing tests here are the ones that feed the SAME
logical data in DIFFERENT column orders: if any of them start failing together,
someone has reintroduced a hardcoded index.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import navfeed  # noqa: E402
import nightly  # noqa: E402
import store    # noqa: E402

# The three layouts AMFI has actually served.
HDR_NEW = ("Scheme Code;ISIN Div Payout/ ISIN Growth;ISIN Div Reinvestment;"
           "Scheme Name;Plan;Option;Net Asset Value;Date")
HDR_OLD = ("Scheme Code;ISIN Div Payout/ISIN Growth;ISIN Div Reinvestment;"
           "Scheme Name;Net Asset Value;Date")
HDR_HIST = ("Scheme Code;NAV Name;Plan;Option;ISIN Div Payout/ISIN Growth;"
            "ISIN Div Reinvestment;Net Asset Value;Date")

NEW = f"""{HDR_NEW}

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

PPFAS Mutual Fund
122639;INF879O01019;INF879O01027;Parag Parikh Flexi Cap Fund;Direct Plan;Growth;98.2900;21-Aug-2026
122640;INF879O01035;-;Parag Parikh Flexi Cap Fund;Regular Plan;IDCW;26.5400;21-Aug-2026
"""

OLD = f"""{HDR_OLD}

Open Ended Schemes(Equity Scheme - Flexi Cap Fund)

PPFAS Mutual Fund
122639;INF879O01019;INF879O01027;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;98.2900;21-Aug-2026
"""

HIST = f"""{HDR_HIST}

Open Ended Schemes ( Flexi Cap )

PPFAS Mutual Fund
122639;Parag Parikh Flexi Cap Fund - Direct Plan - Growth;Direct Plan;Growth;INF879O01019;INF879O01027;98.2900;21-Aug-2026
"""


def _one(text, code="122639"):
    return next(r for r in navfeed.iter_rows(text) if r["code"] == code)


# ── The regression that caused the outage ────────────────────────────────────

def test_new_format_reads_nav_and_date_from_the_shifted_columns():
    """Plan+Option pushed NAV from field 4 to 6 and Date from 5 to 7."""
    r = _one(NEW)
    assert r["nav"] == 98.29
    assert r["date"] == "2026-08-21"


def test_every_layout_yields_the_same_nav_and_date():
    """Same scheme, same numbers, three different column orders. This is the
    test that a hardcoded index cannot pass."""
    for text in (NEW, OLD, HIST):
        r = _one(text)
        assert (r["nav"], r["date"]) == (98.29, "2026-08-21")


def test_scheme_name_survives_moving_between_field_1_and_field_3():
    assert _one(NEW)["name"] == "Parag Parikh Flexi Cap Fund"
    assert _one(HIST)["name"].startswith("Parag Parikh Flexi Cap Fund")


def test_plan_and_option_are_read_when_present_and_empty_when_not():
    assert (_one(NEW)["plan"], _one(NEW)["option"]) == ("Direct Plan", "Growth")
    assert (_one(OLD)["plan"], _one(OLD)["option"]) == ("", "")


def test_a_future_column_insertion_does_not_break_the_parse():
    """The next time AMFI adds a column, this must keep working."""
    text = (HDR_NEW.replace("Net Asset Value", "Some New Column;Net Asset Value")
            + "\n122639;A;B;Fund;Direct Plan;Growth;XYZ;98.29;21-Aug-2026\n")
    assert (_one(text)["nav"], _one(text)["date"]) == (98.29, "2026-08-21")


# ── Row-level robustness ─────────────────────────────────────────────────────

def test_unparseable_and_dead_rows_are_skipped_not_fatal():
    text = HDR_NEW + "\n" + "\n".join([
        "1;A;B;Suspended Fund;Direct Plan;Growth;N.A.;21-Aug-2026",
        "2;A;B;Zero Fund;Direct Plan;Growth;0;21-Aug-2026",
        "3;A;B;Negative Fund;Direct Plan;Growth;-5;21-Aug-2026",
        "4;A;B;Bad Date Fund;Direct Plan;Growth;10.5;not-a-date",
        "5;A;B;Good Fund;Direct Plan;Growth;10.5;21-Aug-2026",
    ])
    assert [r["code"] for r in navfeed.iter_rows(text)] == ["5"]


def test_section_banners_amc_lines_and_preheader_rows_are_ignored():
    text = "\n".join([
        "999;A;B;Orphan Before Any Header;Direct Plan;Growth;10;21-Aug-2026",
        HDR_NEW,
        "Open Ended Schemes(Equity Scheme - Large Cap Fund)",
        "HDFC Mutual Fund",
        "101762;A;B;HDFC Large Cap Fund;Direct Plan;Growth;10;21-Aug-2026",
    ])
    assert [r["code"] for r in navfeed.iter_rows(text)] == ["101762"]


def test_a_header_that_names_no_nav_column_does_not_latch():
    """Garbage must yield nothing, so the caller's floor can fire."""
    assert list(navfeed.iter_rows("Scheme Code;Foo;Bar\n1;2;3\n")) == []


# ── Aggregation helpers ──────────────────────────────────────────────────────

def test_latest_by_scheme_keeps_the_newest_date():
    text = HDR_NEW + "\n" + "\n".join([
        "1;A;B;F;Direct Plan;Growth;10.0;19-Aug-2026",
        "1;A;B;F;Direct Plan;Growth;12.0;21-Aug-2026",
        "1;A;B;F;Direct Plan;Growth;11.0;20-Aug-2026",
    ])
    assert navfeed.latest_by_scheme(text) == {"1": ("2026-08-21", 12.0)}


def test_series_by_scheme_returns_every_date_ascending_and_deduped():
    text = HDR_NEW + "\n" + "\n".join([
        "1;A;B;F;Direct Plan;Growth;12.0;21-Aug-2026",
        "1;A;B;F;Direct Plan;Growth;10.0;19-Aug-2026",
        "1;A;B;F;Direct Plan;Growth;10.0;19-Aug-2026",
    ])
    assert navfeed.series_by_scheme(text) == {
        "1": [["2026-08-19", 10.0], ["2026-08-21", 12.0]]}


# ── The guard that turns a parse failure into a red run ──────────────────────

def test_a_short_parse_is_fatal_rather_than_a_silent_no_op(monkeypatch):
    """The 2026-08 failure mode: every row unparseable, reported as 'weekend'.

    main() must now raise before it ever reaches the store — both because the
    run has to go red, and because there is no point downloading 38MB to
    compare against nothing.
    """
    assert len(nightly.parse_navall(OLD)) < nightly.MIN_SCHEMES

    monkeypatch.setattr(nightly, "fetch_navall", lambda: OLD)
    monkeypatch.setattr(store, "sh", _must_not_run)
    monkeypatch.setattr(store, "download", _must_not_run)
    with pytest.raises(SystemExit, match="format or content has changed"):
        nightly.main()


def _must_not_run(*args, **kw):
    raise AssertionError(
        f"main() touched the store before validating the feed: {args}")


def test_compute_as_of_ignores_amfi_forward_dated_weekend_navs():
    """A handful of liquid schemes carry Saturday NAVs on Friday. as_of must
    name the date the universe is current to, not the furthest row present."""
    schemes = {str(i): [["2026-08-21", 10.0]] for i in range(1000)}
    for i in range(5):                       # 0.5% - under the 1% threshold
        schemes[str(i)] = [["2026-08-21", 10.0], ["2026-08-22", 10.1]]
    assert store.compute_as_of(schemes) == "2026-08-21"


def test_compute_as_of_never_names_a_future_date():
    schemes = {"1": [["2099-01-01", 10.0]], "2": [["2026-08-21", 10.0]]}
    assert store.compute_as_of(schemes) == "2026-08-21"


def test_backfill_merge_inserts_interior_dates_and_never_overwrites():
    import backfill_range as bf
    series = [["2026-08-18", 1.0], ["2026-08-21", 4.0]]
    n = bf.merge(series, [["2026-08-19", 2.0], ["2026-08-20", 3.0],
                          ["2026-08-21", 99.0]])
    assert n == 2
    assert series == [["2026-08-18", 1.0], ["2026-08-19", 2.0],
                      ["2026-08-20", 3.0], ["2026-08-21", 4.0]]


# ── Plausibility: a feed we read WRONGLY, not one we cannot read ─────────────
#
# MIN_SCHEMES catches an unparseable file. These catch a parseable one whose
# numbers come off the wrong column — 14,000 valid-looking floats that would
# sail past every other guard here.

def _store(n, nav=100.0):
    return {"schemes": {str(i): [["2026-08-20", nav]] for i in range(n)}}


def _run(monkeypatch, daily, blob):
    """main() with the feed stubbed and the store in memory."""
    monkeypatch.setattr(nightly, "parse_navall", lambda _t: daily)
    monkeypatch.setattr(nightly, "fetch_navall", lambda: "")
    monkeypatch.setattr(store, "download", lambda repo: (pathlib.Path("."), blob))
    published = {}
    monkeypatch.setattr(store, "publish",
                        lambda w, b, r, **kw: published.update(kw) or "2026-08-21")
    nightly.main()
    return published


def test_a_column_swap_that_still_parses_is_caught(monkeypatch):
    """The residual risk after the header fix: every row parses, but the NAVs
    are somebody else's numbers. Every scheme moves at once."""
    blob = _store(6000)
    daily = {str(i): ("2026-08-21", 4321.0) for i in range(6000)}
    with pytest.raises(SystemExit, match="wrong column"):
        _run(monkeypatch, daily, blob)
    # and nothing was written
    assert all(len(s) == 1 for s in blob["schemes"].values())


def test_a_normal_day_passes(monkeypatch):
    blob = _store(6000)
    daily = {str(i): ("2026-08-21", 100.0 + (i % 7) * 0.3) for i in range(6000)}
    assert _run(monkeypatch, daily, blob)["appended"] == 6000


def test_genuine_redenominations_are_kept_not_rejected(monkeypatch):
    """2013-04-22: a wave of face-value changes, ~9,900% each, all real. The
    worst real day on record was 1.21% of schemes — under the ceiling."""
    blob = _store(6000)
    daily = {str(i): ("2026-08-21", 100.3) for i in range(6000)}
    for i in range(60):                              # 1.0%, like the real day
        daily[str(i)] = ("2026-08-21", 10030.0)      # x100 redenomination
    assert _run(monkeypatch, daily, blob)["appended"] == 6000
    assert blob["schemes"]["0"][-1] == ["2026-08-21", 10030.0]


def test_the_ceiling_sits_above_the_worst_real_day_and_below_a_swap():
    assert nightly.MAX_IMPLAUSIBLE > 0.0121          # 2013-04-22
    assert nightly.MAX_IMPLAUSIBLE < 0.50            # any systemic mis-read


def test_a_tiny_append_is_not_judged_on_its_ratio(monkeypatch):
    """A full feed, but the store is already current for all but three schemes
    — one of which split. That is a 33% breach rate on a sample of 3, which
    means nothing. The ratio must not fire here."""
    blob = _store(6000)
    for i in range(3, 6000):                      # already have 2026-08-21
        blob["schemes"][str(i)] = [["2026-08-21", 100.0]]
    daily = {str(i): ("2026-08-21", 100.1) for i in range(6000)}
    daily["0"] = ("2026-08-21", 10000.0)          # a genuine redenomination
    published = _run(monkeypatch, daily, blob)
    assert published["appended"] == 3
    assert blob["schemes"]["0"][-1] == ["2026-08-21", 10000.0]
