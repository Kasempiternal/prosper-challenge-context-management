import itertools
import json
import time

import httpx
import pytest

from scheduling.decision import DECLINE, Gate, Verdict
from scheduling.jev import JevClient, JevProviderChooser, JevTypeDisambiguator
from scheduling.names import clue_words, match_providers
from scheduling.policy import Patient, check
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

EXISTING_REF = {"is_new": False, "has_referral": True}
GATE = Gate(act_p=0.8, margin=0.5, pair_p=0.85)


# ---- gate --------------------------------------------------------------------------------

@pytest.mark.parametrize("probs, expected", [
    ({"a": 0.95, "b": 0.05}, Verdict(act="a")),
    ({"a": 0.79, "b": 0.21}, Verdict(pair=("a", "b"))),
    ({"a": 0.82, "b": 0.18}, Verdict(act="a")),
    ({"a": 0.6, "b": 0.3, "c": 0.1}, Verdict(pair=("a", "b"))),
    ({"a": 0.5, "b": 0.2, "c": 0.2, "d": 0.1}, Verdict()),
    ({"a": 1.0}, Verdict(act="a")),
    ({}, Verdict()),
])
def test_gate(probs, expected):
    got = GATE.decide(probs)
    assert (got.act, got.pair) == (expected.act, expected.pair)


def test_gate_margin_blocks_act():
    assert Gate(act_p=0.5, margin=0.4, pair_p=0.9).decide({"a": 0.6, "b": 0.35}).pair == ("a", "b")


# ---- client ------------------------------------------------------------------------------

def _answer(probs: dict[str, float], tokens: int = 2455) -> dict:
    choice = max(probs, key=probs.get)
    return {"answers": {"pick": {"type": "choice", "choice": choice, "confidence": probs[choice],
                                 "probabilities": probs}}, "usage": {"input_tokens": tokens, "output_tokens": 30}}


class Server:
    """Scripted JEV endpoint: `script` maps a substring of the request state to probabilities,
    or to an exception class to raise."""

    def __init__(self, script=None, default=None):
        self.script = script or {}
        self.default = default
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        for needle, outcome in self.script.items():
            if needle in body["state"]:
                if isinstance(outcome, type) and issubclass(outcome, Exception):
                    raise outcome("scripted", request=request)
                if isinstance(outcome, int):
                    return httpx.Response(outcome, text="boom")
                return httpx.Response(200, json=_answer(outcome))
        if self.default is None:
            return httpx.Response(200, json=_answer({"zzz": 1.0}))
        return httpx.Response(200, json=_answer(self.default))


def _client(server: Server, mode="auto", cache_path=None) -> JevClient:
    return JevClient("test-key", mode=mode, cache_path=cache_path, transport=httpx.MockTransport(server))


def test_client_returns_probabilities_filtered_to_criteria():
    server = Server({"x": {"a": 0.7, "b": 0.2, "ghost": 0.1}})
    ans = _client(server).choice("x", "q?", {"a": "A", "b": "B"})
    assert ans.probabilities == {"a": 0.7, "b": 0.2}
    assert server.requests[0]["questions"]["pick"]["criteria"] == {"a": "A", "b": "B"}


def test_timeout_returns_none_without_retry():
    server = Server({"x": httpx.ReadTimeout})
    client = _client(server)
    assert client.choice("x", "q?", {"a": "A"}) is None
    assert len(server.requests) == 1
    assert client.calls[-1].source == "failed"


def test_live_default_never_retries():
    server = Server({"x": httpx.ConnectError})
    assert _client(server).choice("x", "q?", {"a": "A"}) is None
    assert len(server.requests) == 1


def test_connect_error_retried_once_when_configured():
    server = Server({"x": httpx.ConnectError})
    client = JevClient("k", retries=1, transport=httpx.MockTransport(server))
    assert client.choice("x", "q?", {"a": "A"}) is None
    assert len(server.requests) == 2


def test_connect_error_then_success():
    attempts = []

    def flaky(request):
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("refused", request=request)
        return httpx.Response(200, json=_answer({"a": 1.0}))

    client = JevClient("k", retries=1, transport=httpx.MockTransport(flaky))
    assert client.choice("x", "q?", {"a": "A"}).probabilities == {"a": 1.0}
    assert len(attempts) == 2


@pytest.mark.parametrize("response", [
    httpx.Response(500, text="boom"),
    httpx.Response(200, text="not json"),
    httpx.Response(200, json={"answers": {}}),
    httpx.Response(200, json={"answers": {"pick": {"choice": "a", "probabilities": "nope"}}}),
])
def test_bad_responses_return_none(response):
    client = JevClient("k", transport=httpx.MockTransport(lambda r: response))
    assert client.choice("x", "q?", {"a": "A"}) is None


def test_disk_cache_round_trip_and_offline_mode(tmp_path):
    path = tmp_path / "cache.json"
    server = Server({"x": {"a": 0.9, "b": 0.1}})
    live = _client(server, mode="live", cache_path=path)
    first = live.choice("x", "q?", {"a": "A", "b": "B"})
    live.save()
    assert len(server.requests) == 1

    offline_server = Server()
    offline = _client(offline_server, mode="cache", cache_path=path)
    again = offline.choice("x", "q?", {"a": "A", "b": "B"})
    assert again == first
    assert offline_server.requests == []
    assert [c.source for c in offline.calls] == ["cache"]
    assert offline.calls[0].input_tokens == 2455

    assert offline.choice("x", "q?", {"a": "A", "b": "B"}) == first
    assert offline.calls[-1].source == "memo"
    assert offline.choice("never seen", "q?", {"a": "A"}) is None
    assert offline_server.requests == []


def test_live_mode_refreshes_cache(tmp_path):
    path = tmp_path / "cache.json"
    _client(Server({"x": {"a": 0.9, "b": 0.1}}), mode="live", cache_path=path).choice("x", "q?", {"a": "A", "b": "B"})
    old = _client(Server(), mode="cache", cache_path=path)
    refreshed = _client(Server({"x": {"a": 0.2, "b": 0.8}}), mode="live", cache_path=path)
    refreshed.choice("x", "q?", {"a": "A", "b": "B"})
    refreshed.save()
    assert _client(Server(), mode="cache", cache_path=path).choice("x", "q?", {"a": "A", "b": "B"}).probabilities == \
        {"a": 0.2, "b": 0.8}
    assert old.calls == []


def test_no_key_never_calls_network():
    server = Server()
    client = JevClient(None, transport=httpx.MockTransport(server))
    assert client.choice("x", "q?", {"a": "A"}) is None
    assert server.requests == []


# ---- clue detection ----------------------------------------------------------------------

@pytest.mark.parametrize("phrase, clue", [
    ("Dr. Chen", ()),
    ("Can I see Dr. Chen please", ()),
    ("Dr. Shen", ()),
    ("Dr. Nwin", ()),
    ("Dr. Chen, the heart doctor", ("heart",)),
    ("Dr. Chen, the woman", ("woman",)),
    ("Dr. Garcia who speaks Spanish", ("speaks", "spanish")),
    ("Maria Garcia, the nurse practitioner", ("nurse", "practitioner")),
    ("Dr. Nguyen, he works at Richmond", ("he", "works", "richmond")),
    ("Dr. Sato, the one I saw last time", ("saw", "last", "time")),
])
def test_clue_words(index, phrase, clue):
    cands = [c.id for c in match_providers(index, phrase)]
    assert len(cands) >= 2
    assert clue_words(index, phrase, cands) == clue


def test_name_with_clue_still_matches_every_same_surname(index):
    chens = {c.id for c in match_providers(index, "Dr. Chen")}
    assert {c.id for c in match_providers(index, "Dr. Chen, the heart doctor")} == chens
    assert {c.id for c in match_providers(index, "Dr. Garcia who speaks Spanish")} == \
        {"prov_002", "prov_003", "prov_008"}


# ---- resolver with JEV hooks -------------------------------------------------------------

def _plan(index, availability, args, server=None, client=None):
    client = client or (_client(server) if server else None)
    hooks = {"disambiguator": JevTypeDisambiguator(index, client, GATE),
             "chooser": JevProviderChooser(index, client, GATE)} if client else {}
    return resolve(index, merge(Request(), Update.from_args(args)), availability, **hooks)


def _same(a, b):
    return (a.status, a.say, a.ask, a.offers, a.refusal) == (b.status, b.say, b.ask, b.offers, b.refusal)


def _types(plan):
    return {o.type_id for o in plan.offers}


def _provs(plan):
    return {o.provider_id for o in plan.offers}


def test_no_lexical_match_act(index, availability):
    server = Server({"back pain": {"appt_032": 0.95, "appt_007": 0.05}})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "something for my back pain"}, server)
    assert plan.status == "offer" and _types(plan) == {"appt_032"}
    criteria = server.requests[0]["questions"]["pick"]["criteria"]
    assert len(criteria) == 74 and not set(criteria) & index.unoffered_types


def test_no_lexical_match_pair_asks_either_or(index, availability):
    server = Server({"yearly exam": {"appt_002": 0.79, "appt_003": 0.21}})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "my yearly exam"}, server)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", ("appt_002", "appt_003"))


def test_specialty_default_only_is_refined(index, availability):
    server = Server({"lung test": {"appt_079": 1.0}})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "lung test"}, server)
    assert _types(plan) == {"appt_079"}


LOW_CONFIDENCE = {"appt_002": 0.3, "appt_003": 0.3, "appt_078": 0.2, "prov_000": 0.2, "prov_046": 0.2}


@pytest.mark.parametrize("outcome", [httpx.ReadTimeout, httpx.ConnectError, 503, LOW_CONFIDENCE])
@pytest.mark.parametrize("args", [
    {**EXISTING_REF, "service_phrase": "lung test"},
    {**EXISTING_REF, "service_phrase": "something for my back pain"},
    {"is_new": False, "service_phrase": "routine checkup for my job"},
    {**EXISTING_REF, "service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen, the woman"},
])
def test_failure_or_low_confidence_equals_no_jev(index, availability, args, outcome):
    with_jev = _plan(index, availability, args, Server(default=None, script={"": outcome}))
    assert _same(with_jev, _plan(index, availability, args))


def test_strong_lexical_match_never_calls(index, availability):
    server = Server()
    for phrase in ("flu shot", "knee MRI", "cardiology consultation"):
        _plan(index, availability, {**EXISTING_REF, "service_phrase": phrase}, server)
    assert server.requests == []


def test_unoffered_stays_lexical(index, availability):
    server = Server(default={"appt_026": 1.0})
    plan = _plan(index, availability, {"service_phrase": "eye exam"}, server)
    assert (plan.status, plan.refusal.code) == ("refuse", "not_offered")
    assert server.requests == []


def test_policy_invalid_pick_takes_refusal_path(index, availability):
    server = Server({"lung test": {"appt_079": 1.0}})
    plan = _plan(index, availability, {"is_new": True, "has_referral": True, "service_phrase": "lung test"}, server)
    assert (plan.status, plan.refusal.code) == ("refuse", "new_patient_type")


def test_confident_pick_outside_lexical_tie_restarts_from_it(index, availability):
    server = Server({"expecting": {"appt_042": 0.97, "appt_002": 0.03}})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "checkups while I'm expecting"}, server)
    assert _types(plan) == {"appt_042"}


def test_bare_name_never_calls_chooser(index, availability):
    server = Server(default={"prov_046": 1.0})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "cardiology consultation",
                                       "provider_phrase": "Can I see Dr. Chen?"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("prov_000", "prov_046"))
    assert server.requests == []


def test_clue_lets_chooser_act(index, availability):
    server = Server({"the woman": {"prov_046": 0.97, "prov_000": 0.03}})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "cardiology consultation",
                                       "provider_phrase": "Dr. Chen, the woman"}, server)
    assert _provs(plan) == {"prov_046"}
    assert set(server.requests[0]["questions"]["pick"]["criteria"]) == {"prov_000", "prov_046"}


def test_policy_decides_before_chooser(index, availability):
    server = Server(default={"prov_000": 1.0})
    plan = _plan(index, availability, {"is_new": True, "has_referral": True, "service_phrase": "cardiology consultation",
                                       "provider_phrase": "Dr. Chen, the guy"}, server)
    assert _provs(plan) == {"prov_046"}
    assert server.requests == []


def test_chooser_pick_with_pending_policy_asks(index, availability):
    server = Server({"guy": {"prov_000": 1.0}})
    plan = _plan(index, availability, {"has_referral": True, "service_phrase": "cardiology consultation",
                                       "provider_phrase": "Dr. Chen, the guy"}, server)
    # David Chen is closed to new patients, so whether the caller is new decides it.
    assert (plan.status, plan.ask.field) == ("ask", "is_new")


def test_chooser_pair_asks_between_two(index, availability):
    server = Server({"Mission Bay": {"prov_004": 0.5, "prov_047": 0.48, "prov_012": 0.02}})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "annual physical",
                                       "provider_phrase": "Dr. Chen who works at Mission Bay"}, server)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", ("prov_004", "prov_047"))


# ---- property: adversarial model never produces a policy violation ------------------------

class Adversary:
    """Always confident, always trying to break policy: picks the candidate most likely to be
    invalid for the patient, or an id that was never a candidate."""

    def __init__(self, index, mode):
        self.ix, self.mode = index, mode

    def pick_type(self, phrase, hint, candidate_ids):
        bad = [t for t in candidate_ids if not self.ix.types[t].new_patients_allowed and self.ix.types[t].requires_referral]
        if self.mode == "outside":
            return Verdict(act=sorted(self.ix.unoffered_types)[0], called=True)
        if self.mode == "pair":
            return Verdict(pair=((bad or candidate_ids)[0], sorted(self.ix.unoffered_types)[0]), called=True)
        return Verdict(act=(bad or candidate_ids)[len(phrase) % len(bad or candidate_ids)], called=True)

    def pick_provider(self, phrase, type_id, candidate_ids):
        if self.mode == "outside":
            return Verdict(act="prov_000", called=True)
        if self.mode == "pair":
            return Verdict(pair=("prov_000", "prov_036"), called=True)
        closed = [p for p in candidate_ids if not self.ix.providers[p].accepting_new_patients]
        return Verdict(act=(closed or candidate_ids)[-1], called=True)


def _assert_passes(plan, index):
    for o in list(plan.offers) + ([plan.confirm] if plan.confirm else []):
        row = index.row(o.type_id, o.provider_id, o.location_id)
        assert row is not None and check(row, plan.req.patient) == [], (o, plan.req.patient)


FLAGS = (True, False, None)
PATIENTS = [Patient(is_new=n, has_referral=r) for n, r in itertools.product(FLAGS, FLAGS)]


def _flags(p):
    return {k: v for k, v in (("is_new", p.is_new), ("has_referral", p.has_referral)) if v is not None}


@pytest.mark.parametrize("mode", ["bad", "outside", "pair"])
def test_property_adversarial_model_never_offers_a_violation(index, availability, mode):
    adv = Adversary(index, mode)
    hooks = {"disambiguator": adv, "chooser": adv}
    sites = {loc.id: loc.short_name for loc in index.locations.values()}
    checked = 0
    for patient in PATIENTS:
        for phrase in ("zzqx blorp", "heart", "lung test", "checkup", "skin check", "my back"):
            plan = resolve(index, merge(Request(), Update.from_args({"service_phrase": phrase, **_flags(patient)})),
                           availability, **hooks)
            _assert_passes(plan, index)
            checked += 1
        for row in index.bookable:
            req = merge(Request(), Update.from_args({
                "service_name": row.type.name,
                "provider_phrase": f"Dr. {row.provider.last_name}, the one at {sites[row.location.id]}",
                **_flags(patient)}))
            plan = resolve(index, req, availability, **hooks)
            _assert_passes(plan, index)
            if plan.status == "offer":
                picked = resolve(index, merge(plan.req, Update.from_args({"pick_offer": 1})), availability, **hooks)
                _assert_passes(picked, index)
            checked += 1
    assert checked > 5000


def test_property_adversarial_jev_client_never_offers_a_violation(index, availability):
    """Same property through the real JEV hooks: the fake endpoint puts p=1.0 on the option most
    likely to break policy (types closed to new patients, providers not accepting them)."""

    def evil(request):
        criteria = json.loads(request.content)["questions"]["pick"]["criteria"]
        keys = sorted(criteria)
        bad = [k for k in keys if (k in index.types and not index.types[k].new_patients_allowed)
               or (k in index.providers and not index.providers[k].accepting_new_patients)]
        return httpx.Response(200, json=_answer({(bad or keys)[0]: 1.0}))

    client = JevClient("k", transport=httpx.MockTransport(evil))
    hooks = {"disambiguator": JevTypeDisambiguator(index, client, GATE), "chooser": JevProviderChooser(index, client, GATE)}
    called = 0
    for patient in PATIENTS:
        for phrase in ("zzqx blorp", "heart", "lung test", "checkup", "something for my back pain"):
            plan = resolve(index, merge(Request(), Update.from_args({"service_phrase": phrase, **_flags(patient)})),
                           availability, **hooks)
            _assert_passes(plan, index)
        for row in index.bookable:
            req = merge(Request(), Update.from_args({"service_name": row.type.name, **_flags(patient),
                                                     "provider_phrase": f"Dr. {row.provider.last_name}, the tall one"}))
            plan = resolve(index, req, availability, **hooks)
            _assert_passes(plan, index)
            called += any("chooser" in n for n in plan.notes)
    assert called > 100 and len(client.calls) > 100


def _slow(seconds):
    def handler(request):
        time.sleep(seconds)
        return httpx.Response(200, json=_answer({"a": 1.0}))
    return httpx.MockTransport(handler)


def test_total_wait_is_bounded_even_when_the_server_hangs():
    client = JevClient("k", timeout_s=0.3, transport=_slow(2.0))
    t0 = time.perf_counter()
    assert client.choice("x", "q?", {"a": "A"}) is None
    assert time.perf_counter() - t0 < 0.6


def test_turn_budget_shared_by_both_consults():
    client = JevClient("k", timeout_s=1.0, turn_budget_s=0.5, transport=_slow(0.35))
    client.begin_turn()
    assert client.choice("first", "q?", {"a": "A"}) is not None
    t0 = time.perf_counter()
    assert client.choice("second", "q?", {"a": "A"}) is None  # only ~0.15 s left of the turn
    assert time.perf_counter() - t0 < 0.3
    client.begin_turn()
    assert client.choice("second", "q?", {"a": "A"}) is not None


def test_warm_up_has_its_own_longer_timeout():
    client = JevClient("k", timeout_s=0.1, transport=_slow(0.4))
    client.begin_turn()
    assert client.warm_up({"a": "A"}) is not None
    assert client.choice("x", "q?", {"a": "A"}) is None


@pytest.mark.parametrize("phrase", ["checkup", "MRI", "follow-up", "skin check", "new patient appointment"])
def test_bare_confusable_tie_never_calls(index, availability, phrase):
    server = Server(default={"appt_002": 1.0, "appt_063": 1.0})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": phrase}, server)
    assert plan.status == "ask" and server.requests == []
