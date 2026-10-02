import json

import httpx
import pytest

from agent_tools.context import load_index, resolve_catalog_path, warm_up_criteria
from scheduling.jev import MAX_CHOICE_OPTIONS, JevClient, JevTypeDisambiguator, type_criteria
from scheduling.lexicon import SHORTLIST_SIZE, ranked_types


class Recorder:
    """A JEV endpoint that answers like the real one, including its 400 on too many options."""

    def __init__(self):
        self.bodies: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        criteria = body["questions"]["pick"]["criteria"]
        if len(criteria) > 255:
            return httpx.Response(400, json={"error": "TypeSafe Choice questions support at most 255 options"})
        first = next(iter(criteria))
        return httpx.Response(200, json={"answers": {"pick": {"type": "choice", "choice": first, "confidence": 1.0,
                                                              "probabilities": {first: 1.0}}},
                                         "usage": {"input_tokens": 10}})


def client(recorder: Recorder) -> JevClient:
    return JevClient("k", mode="live", transport=httpx.MockTransport(recorder))


@pytest.fixture(scope="module")
def national():
    return load_index(resolve_catalog_path("data/national/catalog.json"))


def test_a_choice_over_the_limit_keeps_the_best_ranked_options():
    recorder = Recorder()
    criteria = {f"t{i:03d}": f"type {i}" for i in range(300)}
    ranking = ["t299", "t298", "t000"]
    answer = client(recorder).choice("said", "which?", criteria, ranking=ranking)

    sent = recorder.bodies[0]["questions"]["pick"]["criteria"]
    assert answer is not None
    assert len(sent) == MAX_CHOICE_OPTIONS == 255
    assert {"t299", "t298", "t000"} <= set(sent)
    assert list(sent) == sorted(sent)  # same order as the uncapped request
    assert "t253" not in sent and "t252" in sent  # unranked ones fill the rest in the given order


def test_a_choice_under_the_limit_is_sent_unchanged():
    recorder = Recorder()
    criteria = {"b": "B", "a": "A"}
    client(recorder).choice("said", "which?", criteria, ranking=["a"])
    assert recorder.bodies[0]["questions"]["pick"]["criteria"] == criteria


def test_the_type_hook_caps_every_offered_national_type_by_lexical_rank(national):
    recorder = Recorder()
    jev = client(recorder)
    offered = [t for t in national.types if t not in national.unoffered_types]
    assert len(offered) > 255
    JevTypeDisambiguator(national, jev).pick_type("knee injury", None, offered)

    sent = recorder.bodies[0]["questions"]["pick"]["criteria"]
    assert len(sent) == 255
    assert set(ranked_types(national, "knee injury", None)) & set(offered) <= set(sent)
    assert [c.source for c in jev.calls] == ["live"]  # answered, not a 400


@pytest.mark.parametrize("catalog", ["data/catalog.json", "data/national/catalog.json"])
def test_warm_up_sends_a_shortlist_shaped_request(catalog):
    index = load_index(resolve_catalog_path(catalog))
    recorder = Recorder()
    criteria = warm_up_criteria(index)
    call = client(recorder).warm_up(criteria)

    assert call is not None and call.source == "live"
    body = recorder.bodies[0]
    assert 1 < len(body["questions"]["pick"]["criteria"]) <= SHORTLIST_SIZE
    assert body["questions"]["pick"]["criteria"] == type_criteria(index, list(criteria))
    assert body["questions"]["pick"]["instructions"] == "Which appointment type is the caller asking for?"
    assert body["state"].startswith("A patient calling a multi-specialty clinic said they want: ")
