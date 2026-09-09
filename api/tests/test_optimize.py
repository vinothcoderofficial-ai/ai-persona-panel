"""POST /optimize -- the placement ranking, over HTTP (S29).

`analytics/optimizer.py` (S24) and `analytics/slot_value.py` (S25) have scored
every placement and ranked them since Day 8, and the only way to see any of it
was `python scripts/optimize.py`. The pitch this project makes - not an A/B
testing tool but a recommendation engine, because it models purchase rather
than attention - had no screen, so from the running product it was not a claim
anybody could check.

This endpoint is the seam. It composes the two libraries and serialises what
they return; **no ranking maths lives here or in the web app**, for the same
reason `resolve()` lives in one module and the attention formula in another.

The three things this suite is really protecting, all of them honesty
properties the optimizer already has and that a careless serialiser would
quietly drop:

1. **An undefined objective stays undefined.** `Scored.objective` is None when
   the metric does not exist for a configuration - a creative taken down has no
   ad-to-purchase lift. Serialising that as 0.0 would rank "no advertising at
   all" as a measured, mediocre result rather than as an unanswerable question.
2. **A seed spread is never called a confidence interval.** It is Monte Carlo
   run-to-run variability, and `SeedSpread`'s docstring is emphatic about the
   difference. The payload carries the seeds it was taken over, because a
   spread quoted without its `n_seeds` says nothing.
3. **Skipped candidates are reported.** A shelf level the planogram cannot
   express is not a placement that scored badly, and silently omitting it turns
   "there was no move to try" into "we tried it and it lost".
4. **A question that was not asked is not a negative answer.**
   `Ranking.beats_current` is None when the comparison could not be made at
   all, and this route flattened that to `[]` -- which the screen renders as
   "No placement clears the current one's spread either." On the committed
   aisle the current placement falls outside `spread_top_n` for 23 of the 24
   focal SKUs, so an unanswered question printed as a definite negative nearly
   every time. It is serialised as JSON null now, and the tests below hold both
   answers apart.
5. **The ranking says which estimator produced it.** There are two Brand Lifts
   in `analytics/lift.py`, they disagree several-fold on this aisle, and this
   endpoint hardcoded the confounded one with no way to ask for the other.
"""
import json
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]


def post(client: TestClient, **body: Any) -> Dict[str, Any]:
    response = client.post("/optimize", json={"creative_id": "AD_1", **body})
    assert response.status_code == 200, response.text
    return response.json()


# A run size small enough to keep the suite quick. The endpoint's honesty
# properties do not depend on it, and `n_synth` is echoed back so a caller can
# never mistake which run they are reading.
FAST = {"n_synth": 200, "spread_seeds": [42, 43]}


def test_ranks_every_placement_best_first(client: TestClient) -> None:
    body = post(client, **FAST)

    ranks = [entry["rank"] for entry in body["entries"]]
    assert ranks == sorted(ranks)
    assert ranks[0] == 1
    assert body["n_candidates"] == len(body["entries"])


def test_names_the_objective_it_ranked_on(client: TestClient) -> None:
    """A ranking quoted without its metric is not a result: the same space
    ranked on ad lift and on a SKU's purchase share gives different orders."""
    body = post(client, **FAST)

    assert "AD_1" in body["objective_name"]
    assert body["objective_name"]


def test_each_entry_describes_the_placement_in_words(client: TestClient) -> None:
    body = post(client, **FAST)

    entry = body["entries"][0]
    assert entry["label"]
    assert entry["candidate_id"]
    assert entry["kind"] == "ad_placement"


def test_each_entry_carries_patches_that_could_be_run(client: TestClient) -> None:
    """A recommendation nobody can act on is a slogan. These patches are the
    shape POST /whatif and schemas/variant.schema.json both take, so the screen
    can offer "try this" rather than only "this is better"."""
    body = post(client, **FAST)

    patches = body["entries"][0]["patches"]
    assert isinstance(patches, list) and patches
    assert all("op" in patch for patch in patches)


def test_an_undefined_objective_is_null_and_never_zero(client: TestClient) -> None:
    body = post(client, **FAST)

    values = [entry["objective"] for entry in body["entries"]]
    assert None in values, "taking AD_1 down has no ad-to-purchase lift to report"
    # And an undefined row sorts last rather than being scored as a bad result.
    undefined_ranks = [e["rank"] for e in body["entries"] if e["objective"] is None]
    defined_ranks = [e["rank"] for e in body["entries"] if e["objective"] is not None]
    assert min(undefined_ranks) > max(defined_ranks)


def test_marks_the_placement_that_is_running_today(client: TestClient) -> None:
    body = post(client, **FAST)

    current = [entry for entry in body["entries"] if entry["is_current"]]
    assert current, "the unchanged planogram must appear in its own ranking"
    assert body["current_rank"] == min(entry["rank"] for entry in current)


def test_reports_the_seed_spread_as_a_spread(client: TestClient) -> None:
    body = post(client, **FAST)

    spread = body["entries"][0]["seed_spread"]
    assert spread is not None
    assert spread["low"] <= spread["high"]
    # The seeds are part of the measurement: a range over two seeds and a range
    # over twenty are different statistics of the same variability.
    assert spread["seeds"] == [42, 43]
    assert len(spread["values"]) == 2


def test_never_calls_the_spread_a_confidence_interval(client: TestClient) -> None:
    """Every mention of the phrase is a denial of it.

    Asserting the phrase is simply absent was the first version of this test,
    and it failed against correct output: the caveat and the summary both say
    "not a confidence interval", which is exactly the claim that has to be
    made. What must never appear is the phrase used affirmatively.
    """
    raw = client.post("/optimize", json={"creative_id": "AD_1", **FAST}).text.lower()

    mentions = raw.count("confidence interval")
    denials = raw.count("not a confidence interval")
    assert mentions > 0, "the payload has to address it, not stay silent"
    assert mentions == denials, "a spread was described as a confidence interval"

    body = post(client, **FAST)
    assert "not a confidence interval" in body["spread_caveat"].lower()


def test_says_whether_the_top_pick_is_actually_separated(client: TestClient) -> None:
    """The recommendation's own weakness, reported rather than hidden. At small
    `n_synth` the leaders routinely overlap, and a screen that printed a winner
    without saying so would be over-claiming."""
    body = post(client, **FAST)

    assert body["top_pick_is_resolved"] in (True, False, None)
    assert isinstance(body["entries"][0]["unresolved_against"], list)


def test_says_which_placements_clear_the_current_one(client: TestClient) -> None:
    """Three answers, not two: a list of ids, an empty list (everything was
    compared, nothing cleared it), or null (the comparison was never made).
    `isinstance(..., list)` was the assertion here, and it passed against a
    serialiser that turned the third answer into the second."""
    body = post(client, **FAST)

    assert body["beats_current"] is None or isinstance(body["beats_current"], list)


def test_reports_skipped_candidates_rather_than_dropping_them(client: TestClient) -> None:
    """With a focal SKU the space includes shelf levels some bays do not have.
    A level missing from the ranking reads as "we tried it and it was bad"."""
    body = post(client, focal_sku_id="SKU_008", **FAST)

    assert isinstance(body["skipped"], list)
    for skip in body["skipped"]:
        assert skip["reason"], "a skip with no reason is just a hole"


def test_a_focal_sku_adds_shelf_level_candidates(client: TestClient) -> None:
    without = post(client, **FAST)
    with_sku = post(client, focal_sku_id="SKU_008", **FAST)

    assert with_sku["n_candidates"] > without["n_candidates"]
    assert any(entry["kind"] == "sku_shelf_level" for entry in with_sku["entries"])


def test_echoes_the_run_it_was_produced_at(client: TestClient) -> None:
    """A ranking is a function of its run size and seed, and two rankings at
    different sizes are not comparable. The payload has to say which it is."""
    body = post(client, **FAST)

    assert body["n_synth"] == 200
    assert body["seed"] == 42
    assert body["variant_id"] == "A"
    assert body["elapsed_ms"] >= 0


def test_a_headline_sentence_is_provided_for_the_screen(client: TestClient) -> None:
    body = post(client, **FAST)

    assert isinstance(body["summary_lines"], list)
    assert body["summary_lines"], "the recommendation in words is the demo shot"
    assert any("AD_1" in line for line in body["summary_lines"])


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------

def test_an_unknown_creative_is_404(client: TestClient) -> None:
    response = client.post("/optimize", json={"creative_id": "AD_NOPE", **FAST})

    assert response.status_code == 404
    assert "AD_NOPE" in response.json()["detail"]


def test_an_unknown_variant_is_404(client: TestClient) -> None:
    response = client.post(
        "/optimize", json={"creative_id": "AD_1", "variant_id": "ZZ", **FAST}
    )

    assert response.status_code == 404


def test_an_unknown_focal_sku_is_404(client: TestClient) -> None:
    response = client.post(
        "/optimize", json={"creative_id": "AD_1", "focal_sku_id": "SKU_999", **FAST}
    )

    assert response.status_code == 404
    assert "SKU_999" in response.json()["detail"]


@pytest.mark.parametrize("n_synth", [0, -1, 10_000_000])
def test_an_impossible_run_size_is_422(client: TestClient, n_synth: int) -> None:
    """Not a silent clamp. A caller who asked for ten million shoppers and got
    ten thousand would be reading a ranking they did not request."""
    response = client.post(
        "/optimize", json={"creative_id": "AD_1", "n_synth": n_synth}
    )

    assert response.status_code == 422


def test_an_unknown_field_is_refused(client: TestClient) -> None:
    """The request model forbids extras, like POST /whatif's: a misspelled
    `n_synths` that was quietly ignored would return a ranking at the default
    run size while the caller believed otherwise."""
    response = client.post(
        "/optimize", json={"creative_id": "AD_1", "n_synths": 200}
    )

    assert response.status_code == 422


def test_the_same_request_twice_gives_the_same_ranking(client: TestClient) -> None:
    """Determinism is the property `scripts/eval.py` depends on, and the one
    that makes a recommendation quotable at all."""
    first = post(client, **FAST)
    second = post(client, **FAST)

    def order(body: Dict[str, Any]) -> List[str]:
        return [entry["candidate_id"] for entry in body["entries"]]

    assert order(first) == order(second)
    assert [e["objective"] for e in first["entries"]] == [
        e["objective"] for e in second["entries"]
    ]


# ---------------------------------------------------------------------------
# Which estimator produced the column
# ---------------------------------------------------------------------------
#
# `analytics/lift.py` carries two Brand Lifts and docs/PHASE3.md P3.1 records
# that on this aisle they disagree several-fold: the within-run split says
# +4.5 % where the between-arm comparison says +0.9 %, because within one run
# "ad exposed" is a selection and not a randomisation. This endpoint ranked on
# the within-run one, with no way to ask for the other, from the day it shipped
# until now. These tests are what stops that returning.


def test_the_default_objective_is_the_between_arm_comparison(client: TestClient) -> None:
    """The default is the estimator a client's own study would produce.

    Not backwards compatibility: a caller who names no objective gets the one
    whose two arms are identical by construction, because a default is what
    almost everybody reads.
    """
    body = post(client, **FAST)

    assert "between-arm" in body["objective_name"]
    assert "AD_1" in body["objective_name"]
    assert body["objective"] == "between_arm_lift"


def test_the_within_run_split_can_be_asked_for_and_is_labelled_as_a_selection(
    client: TestClient,
) -> None:
    """It still has to be reachable -- it is the only estimator a REAL panel can
    produce, since a real panel shops one store -- but it arrives carrying the
    sentence that says why its number is bigger."""
    body = post(client, objective="within_run_lift", **FAST)

    assert "within-run" in body["objective_name"]
    assert "selection" in body["objective_caveat"].lower()


def test_every_ranking_carries_the_sentence_that_says_what_its_number_is(
    client: TestClient,
) -> None:
    """A percentage on a screen with no estimator beside it is an unlabelled
    percentage, and these two estimators differ by a factor of five."""
    for objective in ("between_arm_lift", "within_run_lift"):
        body = post(client, objective=objective, **FAST)
        assert body["objective_caveat"], objective
        assert body["objective_caveat"] in "\n".join(body["summary_lines"])


def test_the_two_estimators_are_not_the_same_measurement(client: TestClient) -> None:
    """Same space, same run size, same seed, same rows -- and different numbers,
    because the difference is the estimator and nothing else."""
    between = post(client, objective="between_arm_lift", **FAST)
    within = post(client, objective="within_run_lift", **FAST)

    assert between["objective_name"] != within["objective_name"]

    def scored(body: Dict[str, Any]) -> Dict[str, Any]:
        return {e["candidate_id"]: e["objective"] for e in body["entries"]}

    assert set(scored(between)) == set(scored(within))
    assert scored(between) != scored(within)


def test_the_purchase_share_objective_needs_no_creative_at_all(client: TestClient) -> None:
    """The objective for a shelf whose advertising is unknown.

    `sku_purchase_share_objective` names no creative, which makes it the only
    one that can rank a planogram reconstructed by `vision/` -- products
    identified, campaigns on the wall not. It had no production caller at all
    before this, so from the running product it did not exist.
    """
    response = client.post(
        "/optimize",
        json={"objective": "sku_purchase_share", "focal_sku_id": "SKU_008", **FAST},
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["objective_name"] == "population purchase share of SKU_008"
    assert body["creative_id"] is None
    assert any(entry["kind"] == "sku_shelf_level" for entry in body["entries"])


def test_the_purchase_share_objective_without_a_focal_sku_is_refused(
    client: TestClient,
) -> None:
    """Not a silent fallback to the lift. "Which SKU's share?" has no default
    that would not be an invention."""
    response = client.post("/optimize", json={"objective": "sku_purchase_share", **FAST})

    assert response.status_code == 422
    assert "focal_sku_id" in response.json()["detail"]


def test_a_lift_objective_without_a_creative_is_refused(client: TestClient) -> None:
    response = client.post("/optimize", json={"objective": "between_arm_lift", **FAST})

    assert response.status_code == 422
    assert "creative_id" in response.json()["detail"]


def test_an_unknown_objective_is_refused_rather_than_defaulted(client: TestClient) -> None:
    """A misspelled objective that fell back to the default would hand back a
    ranking on a metric the caller did not ask for -- the same failure the
    `extra="forbid"` on this model exists to prevent."""
    response = client.post(
        "/optimize", json={"creative_id": "AD_1", "objective": "vibes", **FAST}
    )

    assert response.status_code == 422


def test_each_row_says_whether_its_spread_clears_no_effect(client: TestClient) -> None:
    """The question that comes before the ranking: does this placement do
    anything at all? A row whose seed range contains zero has not been shown
    to, whatever it outranked."""
    body = post(client, **FAST)

    for entry in body["entries"]:
        assert entry["spread_clears_no_effect"] in (True, False, None)
        if entry["seed_spread"] is None:
            assert entry["spread_clears_no_effect"] is None


def test_a_share_ranking_asks_no_row_to_clear_no_effect(client: TestClient) -> None:
    """A purchase share of 0 means "sold nothing", not "did nothing", so the
    question does not exist for that objective and every row answers null."""
    body = post(client, objective="sku_purchase_share", focal_sku_id="SKU_008", **FAST)

    assert all(entry["spread_clears_no_effect"] is None for entry in body["entries"])


# ---------------------------------------------------------------------------
# A question that was not asked is not a negative answer
# ---------------------------------------------------------------------------
#
# `Ranking.beats_current` is None when the comparison could not be made -- no
# candidate reproduces the input planogram, or the current placement fell
# outside `spread_top_n` and so has no range for anything to clear. This route
# used to serialise that as `[]`, and the screen renders `[]` as "No placement
# clears the current one's spread either." On the committed aisle at the
# endpoint's own defaults the current placement ranks 5th to 8th, so it lands
# outside the top five for 23 of the 24 focal SKUs: an unanswered question
# printed as a definite negative, nearly every time.


def test_an_unanswered_beats_current_comparison_is_null_not_an_empty_list(
    client: TestClient,
) -> None:
    body = post(client, spread_top_n=0, **{**FAST, "spread_seeds": [42, 43]})

    assert all(entry["seed_spread"] is None for entry in body["entries"])
    assert body["beats_current"] is None, (
        "with no spreads there is no bar to clear, and [] would read as "
        "'every placement was compared and none cleared it'"
    )


def test_a_beats_current_comparison_that_was_made_comes_back_as_a_list(
    client: TestClient,
) -> None:
    """The other answer, and the reason null has to be distinguishable from it:
    an empty list here is a finding -- everything was compared, nothing won."""
    body = post(client, spread_top_n=50, **FAST)

    current = [entry for entry in body["entries"] if entry["is_current"]]
    assert current and current[0]["seed_spread"] is not None
    assert isinstance(body["beats_current"], list)


# ---------------------------------------------------------------------------
# A planogram the simulator cannot run
# ---------------------------------------------------------------------------


def _planogram_without_a_cached_policy(client: TestClient) -> str:
    """Store a copy of the seed aisle under a new id, and a variant on it.

    `data/cache/policies/` is keyed `{persona_id}_{planogram_id}.json`, so a
    planogram id that has never been through `make seed` has no policy for any
    persona and `api.app.simcache.load_policy` raises FileNotFoundError on the
    first candidate simulated.
    """
    planogram = json.loads(
        (ROOT / "data" / "planograms" / "demo_aisle.json").read_text(encoding="utf-8")
    )
    planogram["planogram_id"] = "unpolicied_aisle"
    assert client.post("/planograms", json=planogram).status_code == 201

    variant = {
        "variant_id": "UNPOLICIED",
        "base_planogram_id": "unpolicied_aisle",
        "name": "an aisle nothing has a policy for",
        "patches": [],
    }
    assert client.post("/variants", json=variant).status_code == 201
    return "UNPOLICIED"


def test_a_planogram_with_no_cached_policy_is_404_and_names_the_missing_file(
    client: TestClient,
) -> None:
    """POST /whatif has handled this since S15; this route did not, so the same
    input that gives one endpoint a 404 gave this one a 500 and a traceback.

    A 500 says "this server is broken". The truth is that the caller asked for
    a planogram nobody has generated persona policies for, which is a thing
    they can fix, and the response has to say which file is missing.
    """
    variant_id = _planogram_without_a_cached_policy(client)

    response = client.post(
        "/optimize", json={"creative_id": "AD_1", "variant_id": variant_id, **FAST}
    )

    assert response.status_code == 404, response.text
    detail = response.json()["detail"]
    assert "unpolicied_aisle" in detail
    assert "data/cache/policies/" in detail
    assert ".json" in detail
