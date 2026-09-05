"""Tests for scripts/collect_link.py (S21).

The link is how ~100 colleagues reach the store, and the assignment it carries
decides which variant each of them shops. Two properties matter and both are
pinned here: the split across variants is balanced (an unbalanced panel makes
the A-vs-B comparison weaker for no reason), and the whole batch is
reproducible from a seed (so "who got what" can be regenerated rather than
stored in a spreadsheet next to people's names).
"""
import collections
import pathlib
import sys
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts import collect_link  # noqa: E402

BASE = "https://shoppertwin.internal.example.com"


def variants_of(assignments):
    return [a.variant_id for a in assignments]


def test_makes_exactly_n_links():
    got = collect_link.assign(12, base_url=BASE, variants=("A", "B", "C"), seed=42)
    assert len(got) == 12


def test_split_is_balanced_across_variants():
    got = collect_link.assign(100, base_url=BASE, variants=("A", "B", "C"), seed=42)
    counts = collections.Counter(variants_of(got))
    assert set(counts) == {"A", "B", "C"}
    assert max(counts.values()) - min(counts.values()) <= 1


def test_balanced_even_when_n_is_not_a_multiple_of_the_variant_count():
    got = collect_link.assign(10, base_url=BASE, variants=("A", "B", "C"), seed=7)
    counts = collections.Counter(variants_of(got))
    assert sorted(counts.values()) == [3, 3, 4]


def test_same_seed_reproduces_the_batch_exactly():
    first = collect_link.assign(30, base_url=BASE, variants=("A", "B", "C"), seed=42)
    again = collect_link.assign(30, base_url=BASE, variants=("A", "B", "C"), seed=42)
    assert [a.url for a in first] == [a.url for a in again]


def test_a_different_seed_shuffles_the_order():
    """Non-vacuity: if the seed did nothing, the test above would prove nothing."""
    first = collect_link.assign(30, base_url=BASE, variants=("A", "B", "C"), seed=42)
    other = collect_link.assign(30, base_url=BASE, variants=("A", "B", "C"), seed=43)
    assert variants_of(first) != variants_of(other)


def test_the_url_carries_the_assigned_variant():
    for assignment in collect_link.assign(9, base_url=BASE, variants=("A", "B"), seed=1):
        query = parse_qs(urlparse(assignment.url).query)
        assert query["variant"] == [assignment.variant_id]


def test_a_base_url_that_already_has_a_query_is_preserved():
    got = collect_link.assign(
        3, base_url=BASE + "/store?utm_source=email", variants=("A",), seed=1
    )
    query = parse_qs(urlparse(got[0].url).query)
    assert query["utm_source"] == ["email"]
    assert query["variant"] == ["A"]


def test_a_trailing_slash_does_not_produce_a_double_slash():
    got = collect_link.assign(1, base_url=BASE + "/", variants=("A",), seed=1)
    assert "//" not in got[0].url.split("://", 1)[1]


def test_an_unknown_variant_is_refused():
    """A link to a variant with no JSON would 404 every shopper who got it."""
    with pytest.raises(collect_link.UnknownVariant):
        collect_link.assign(3, base_url=BASE, variants=("A", "Z"), seed=1)


def test_a_non_positive_n_is_refused():
    with pytest.raises(ValueError):
        collect_link.assign(0, base_url=BASE, variants=("A",), seed=1)


def test_no_variants_is_refused():
    with pytest.raises(ValueError):
        collect_link.assign(3, base_url=BASE, variants=(), seed=1)


def test_an_insecure_base_url_is_refused():
    """getUserMedia needs a secure context; an http:// link kills the webcam arm."""
    with pytest.raises(collect_link.InsecureBaseUrl):
        collect_link.assign(3, base_url="http://example.com", variants=("A",), seed=1)


def test_localhost_over_http_is_allowed():
    """Browsers treat localhost as a secure context, and the demo runs there."""
    got = collect_link.assign(2, base_url="http://localhost:5173", variants=("A",), seed=1)
    assert len(got) == 2


def test_summary_reports_the_split_and_the_plan_target():
    got = collect_link.assign(12, base_url=BASE, variants=("A", "B", "C"), seed=42)
    text = collect_link.summary(got)
    assert "12" in text
    for variant_id in ("A", "B", "C"):
        assert variant_id in text
    # PLAN targets >= 60 accepted; a batch of 12 cannot reach it even if every
    # session were accepted, and the operator should be told so up front.
    assert "60" in text


def test_summary_does_not_warn_when_the_batch_can_reach_the_target():
    got = collect_link.assign(120, base_url=BASE, variants=("A", "B", "C"), seed=42)
    assert "short of" not in collect_link.summary(got)
