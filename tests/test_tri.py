"""scripts/tri.py: parsing niftyindices.com's TRI rows and merging them."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))

import tri


def test_parse_sorts_and_skips_blanks():
    text = ('[{"Index Name":"Nifty 50","Date":"30 Oct 2026","TotalReturnsIndex":"38,239.19"},'
            '{"Index Name":"Nifty 50","Date":"29 Oct 2026","TotalReturnsIndex":"38310.10"},'
            '{"Index Name":"Nifty 50","Date":"28 Oct 2026","TotalReturnsIndex":"-"}]')
    assert tri.parse(text) == [["2026-10-29", 38310.10], ["2026-10-30", 38239.19]]


def test_merge_appends_only_newer_rows():
    series = [["2026-10-29", 1.0]]
    assert tri.merge(series, [["2026-10-29", 9.0], ["2026-10-30", 2.0]]) == 1
    assert series == [["2026-10-29", 1.0], ["2026-10-30", 2.0]]
