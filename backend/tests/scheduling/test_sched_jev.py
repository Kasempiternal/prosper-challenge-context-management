import itertools
import json
import time

import httpx
import pytest

from scheduling.decision import DECLINE, Check, CheckGate, Gate, Verdict, gender_of
from scheduling.jev import JevClient, JevProviderChooser, JevTypeDisambiguator
from scheduling.lexicon import nearest_type, unexplained_words
from scheduling.names import clue_words, match_providers
from scheduling.policy import Patient, check
from scheduling.request import Request, Update, merge
from scheduling.resolver import resolve

EXISTING_REF = {"is_new": False, "has_referral": True}
GATE = Gate(act_p=0.8, margin=0.5, pair_p=0.85)


# ---- gate --------------------------------------------------------------------------------

@pytest.mark.parametrize("probs, expected", [
    ({"a": 0.95, "b": 0.05}, Verdict(act="a")),
    ({"a": 0.79, "b": 0.21}, Verdict(ask=("a", "b"))),
    ({"a": 0.82, "b": 0.18}, Verdict(act="a")),
    ({"a": 0.6, "b": 0.3, "c": 0.1}, Verdict(ask=("a", "b"))),
    ({"a": 0.5, "b": 0.2, "c": 0.2, "d": 0.1}, Verdict()),
    ({"a": 1.0}, Verdict(act="a")),
    ({}, Verdict()),
])
def test_gate(probs, expected):
    got = GATE.decide(probs)
    assert (got.act, got.ask) == (expected.act, expected.ask)


def test_gate_margin_blocks_act():
    assert Gate(act_p=0.5, margin=0.4, pair_p=0.9).decide({"a": 0.6, "b": 0.35}).ask == ("a", "b")


# ---- client ------------------------------------------------------------------------------

def _answer(probs: dict[str, float], tokens: int = 2455) -> dict:
    choice = max(probs, key=probs.get)
    return {"answers": {"pick": {"type": "choice", "choice": choice, "confidence": probs[choice],
                                 "probabilities": probs}}, "usage": {"input_tokens": tokens, "output_tokens": 30}}


class Server:
    """Scripted JEV endpoint: `script` maps a substring of the request state to probabilities,
    or to an exception class to raise."""

    def __init__(self, script=None, default=None, nouls=None, checks=None):
        self.script = script or {}
        self.default = default
        self.nouls = nouls or {}  # substring of a yes/no question -> probability of yes
        self.checks = checks or {}  # substring of the state -> answer to a type check ("either" question)
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        questions = body["questions"]
        if all(q["type"] == "noul" for q in questions.values()):
            answers = {name: {"type": "noul", "noul": next((p for needle, p in self.nouls.items()
                                                            if needle in q["instructions"]), 0.5)}
                       for name, q in questions.items()}
            return httpx.Response(200, json={"answers": answers, "usage": {"input_tokens": 300, "output_tokens": 9}})
        criteria = questions.get("pick", {}).get("criteria", {})
        if "either" in criteria:
            outcome = next((o for needle, o in self.checks.items() if needle in body["state"]),
                           {next(iter(criteria)): 1.0})  # unscripted: the check confirms the choice
            if isinstance(outcome, type) and issubclass(outcome, Exception):
                raise outcome("scripted", request=request)
            return httpx.Response(200, json=_answer(outcome))
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


def _noul_server(values: dict[str, float], seen: list):
    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        answers = {name: {"type": "noul", "noul": values[name]} for name in body["questions"]}
        return httpx.Response(200, json={"answers": answers, "usage": {"input_tokens": 400, "output_tokens": 9}})
    return httpx.MockTransport(handler)


def test_nouls_ask_every_question_in_one_request_and_cache_it(tmp_path):
    path, seen = tmp_path / "cache.json", []
    live = JevClient("k", mode="auto", cache_path=path, transport=_noul_server({"a": 0.9, "b": 0.2}, seen))
    assert live.nouls("state", {"a": "Is a?", "b": "Is b?"}, purpose="provider gender") == {"a": 0.9, "b": 0.2}
    live.save()
    assert len(seen) == 1 and set(seen[0]["questions"]) == {"a", "b"}
    assert (live.calls[0].purpose, live.calls[0].source, live.calls[0].p) == ("provider gender", "live", None)
    offline = JevClient("k", mode="cache", cache_path=path, transport=_noul_server({}, seen))
    assert offline.nouls("state", {"a": "Is a?", "b": "Is b?"}) == {"a": 0.9, "b": 0.2}
    assert len(seen) == 1


def test_a_single_choice_records_its_purpose_and_top_probability():
    client = _client(Server({"x": {"a": 0.7, "b": 0.3}}))
    client.choice("x", "q?", {"a": "A", "b": "B"}, purpose="type check")
    assert (client.calls[0].purpose, client.calls[0].p) == ("type check", 0.7)


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


YEARLY_TWINS = {"either": 0.92, "appt_002": 0.06, "appt_003": 0.02}


def test_no_lexical_match_pair_asks_either_or(index, availability):
    server = Server({"once a year": {"appt_002": 0.79, "appt_003": 0.21}}, checks={"once a year": YEARLY_TWINS})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "my once a year exam"}, server)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", ("appt_002", "appt_003"))


def test_a_confident_choice_of_one_of_two_twins_still_asks(index, availability):
    """A live replay of the cached "my yearly exam" request put 0.80 on Annual Physical; the check says the
    words fit both."""
    server = Server({"once a year": {"appt_002": 0.97, "appt_003": 0.03}}, checks={"once a year": YEARLY_TWINS})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "my once a year exam"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("appt_002", "appt_003"))
    check = server.requests[1]["questions"]["pick"]
    assert list(check["criteria"]) == ["appt_002", "appt_003", "either"]
    assert check["criteria"]["appt_002"].startswith("Annual Physical (Family Medicine; for: ")


def test_a_check_that_names_the_rival_contradicts_the_choice_and_asks(index, availability):
    server = Server({"weird spot": {"appt_026": 0.97, "appt_027": 0.03}},
                    checks={"weird spot": {"appt_026": 0.13, "appt_027": 0.6, "either": 0.27}})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "a weird spot on my arm"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("appt_026", "appt_027"))


@pytest.mark.parametrize("chosen, rival, either, acts", [
    (0.05, 0.20, 0.75, False),  # review round 3, finding 2: the check prefers the rival
    (0.40, 0.40, 0.20, False),  # a tie is no preference
    (0.41, 0.40, 0.19, True),
    (0.15, 0.05, 0.80, False),  # twins
])
def test_a_confident_choice_stands_only_if_its_check_still_prefers_it(chosen, rival, either, acts):
    first = Verdict(act="a", top=(("a", 0.97), ("b", 0.03)), called=True)
    verdict = CheckGate().decide(first, "b", Check(chosen, rival, either))
    assert (verdict.act, verdict.ask) == (("a", None) if acts else (None, ("a", "b")))


@pytest.mark.parametrize("first, rival", [
    ({"appt_018": 0.99, "appt_000": 0.01}, "appt_019"),   # a long shot: School Physical's neighbour, Sports Physical
    ({"appt_018": 0.9, "appt_002": 0.1}, "appt_002"),     # a runner-up at >= 0.05 is the rival
])
def test_the_check_weighs_the_runner_up_or_else_the_nearest_neighbour(index, availability, first, rival):
    server = Server({"zorbly": first})
    _plan(index, availability, {"is_new": False, "service_phrase": "the zorbly paperwork"}, server)
    assert list(server.requests[1]["questions"]["pick"]["criteria"]) == ["appt_018", rival, "either"]


def test_nearest_type_prefers_catalog_confusables_then_the_same_specialty(index):
    offered = sorted(t for t in index.types if t not in index.unoffered_types)
    assert nearest_type(index, "appt_018", offered, "school form", None) == "appt_019"   # confusable
    assert nearest_type(index, "appt_079", offered, "lung test", None) == "appt_078"     # same specialty
    assert nearest_type(index, "appt_079", ["appt_079", "appt_000"], "lung test", None) is None


@pytest.mark.parametrize("lead, either, acts", [(0.65, 0.33, False), (0.66, 0.32, True), (0.9, 0.5, False)])
def test_a_check_settles_a_pair_only_above_the_threshold(lead, either, acts):
    """h2-28 sits at 0.65 exactly: a case on the boundary takes the safe side."""
    first = Verdict(ask=("a", "b"), top=(("a", 0.67), ("b", 0.33)), called=True)
    verdict = CheckGate().decide(first, "b", Check(lead, round(1 - lead - either, 2), either))
    assert (verdict.act, verdict.ask) == (("a", None) if acts else (None, ("a", "b")))


def test_a_check_that_prefers_the_rival_without_a_lead_asks(index, availability):
    server = Server({"weird spot": {"appt_026": 0.97, "appt_027": 0.03}},
                    checks={"weird spot": {"appt_026": 0.05, "appt_027": 0.2, "either": 0.75}})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "a weird spot on my arm"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("appt_026", "appt_027"))


def test_a_sure_check_settles_a_choice_that_left_two(index, availability):
    server = Server({"tummy": {"appt_053": 0.55, "appt_007": 0.43}},
                    checks={"tummy": {"appt_053": 0.93, "appt_007": 0.01, "either": 0.06}})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "my tummy's been hurting for weeks"}, server)
    assert plan.status == "offer" and _types(plan) == {"appt_053"}


def test_a_confident_choice_whose_check_never_came_is_confirmed_not_booked(index, availability):
    server = Server({"zorbly": {"appt_018": 0.99, "appt_000": 0.01}}, checks={"zorbly": httpx.ReadTimeout})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "the zorbly paperwork"}, server)
    assert (plan.status, plan.ask.options, plan.say) == ("ask", ("appt_018",), "Is that a school physical?")


@pytest.mark.parametrize("check", [{"appt_053": 0.6, "appt_007": 0.08, "either": 0.32}, httpx.ReadTimeout])
def test_an_unsure_or_missing_check_asks_between_the_two(index, availability, check):
    server = Server({"tummy": {"appt_053": 0.55, "appt_007": 0.43}}, checks={"tummy": check})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "my tummy's been hurting for weeks"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("appt_007", "appt_053"))


def test_specialty_default_only_is_refined(index, availability):
    server = Server({"lung test": {"appt_079": 1.0}})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "lung test"}, server)
    assert _types(plan) == {"appt_079"}


LOW_CONFIDENCE = {"appt_002": 0.3, "appt_003": 0.3, "appt_078": 0.2, "prov_000": 0.2, "prov_046": 0.2}


MODEL_ARGS = [
    {**EXISTING_REF, "service_phrase": "lung test"},
    {**EXISTING_REF, "service_phrase": "something for my back pain"},
    {"is_new": False, "service_phrase": "routine checkup for my job"},
    {"service_phrase": "shots before my trip to Thailand"},
    {**EXISTING_REF, "service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen, the woman"},
]


@pytest.mark.parametrize("args", MODEL_ARGS)
def test_low_confidence_equals_no_jev(index, availability, args):
    with_jev = _plan(index, availability, args, Server(default=None, script={"": LOW_CONFIDENCE}))
    assert _same(with_jev, _plan(index, availability, args))


@pytest.mark.parametrize("outcome", [httpx.ReadTimeout, httpx.ConnectError, 503])
@pytest.mark.parametrize("args", MODEL_ARGS)
def test_a_model_answer_that_never_came_is_never_committed_on(index, availability, args, outcome):
    plan = _plan(index, availability, args, Server(default=None, script={"": outcome}))
    assert plan.status == "ask"


def test_a_failed_model_asks_what_the_lexicon_understood(index, availability):
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "lung test"},
                 Server(default=None, script={"": httpx.ReadTimeout}))
    assert (plan.ask.field, plan.ask.options, plan.say) == ("service", ("appt_078",),
                                                            "Is that a pulmonology consultation?")


def test_words_the_lexical_match_leaves_unexplained_are_heard_by_the_model(index, availability):
    server = Server({"Thailand": {"appt_013": 0.96, "appt_010": 0.04}},
                    checks={"Thailand": {"appt_013": 0.92, "appt_010": 0.01, "either": 0.07}})
    plan = _plan(index, availability, {"service_phrase": "shots before my trip to Thailand"}, server)
    assert _types(plan) == {"appt_013"}
    check = server.requests[1]["questions"]["pick"]["criteria"]
    assert list(check) == ["appt_013", "appt_010", "either"]  # the choice against the lexical match


@pytest.mark.parametrize("phrase", ["I need a flu shot today", "I need a flu shot next week",
                                    "a flu shot with Dr. Chen", "a flu shot at Mission Bay",
                                    "a flu shot tomorrow morning, as soon as possible"])
def test_words_other_parsers_take_leave_a_clear_phrase_clear(index, availability, phrase):
    server = Server()
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": phrase}, server)
    assert _types(plan) == {"appt_011"} and server.requests == []


@pytest.mark.parametrize("phrase, left", [
    ("an x-ray in the back", ("in", "back")),     # "back" is a visit word, wherever it stands
    ("a flu shot with Dr. Nwin", ("dr", "nwin")),  # a misheard name is no catalog name
    ("a flu shot for my trip", ("trip",)),
])
def test_a_visit_word_or_an_unknown_name_is_still_heard(index, phrase, left):
    assert unexplained_words(index, phrase, ["appt_011", "appt_062"]) == left


def test_an_answer_to_a_type_question_is_heard_among_the_options_asked(index, availability):
    server = Server({"for my job": {"appt_002": 0.95, "appt_003": 0.05}})
    first, second = _talk(index, availability, server, {"is_new": False, "service_phrase": "checkup"},
                          {"service_phrase": "the physical, for my job"})
    assert first.ask.options == ("appt_002", "appt_003")
    assert set(server.requests[0]["questions"]["pick"]["criteria"]) == {"appt_002", "appt_003"}
    assert _types(second) == {"appt_002"}


def test_a_model_that_agrees_with_the_lexical_match_needs_no_check(index, availability):
    server = Server({"for my son": {"appt_019": 0.99}})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "sports physical for my son"}, server)
    assert _types(plan) == {"appt_019"} and len(server.requests) == 1


def test_a_model_with_nothing_to_say_keeps_the_lexical_match(index, availability):
    server = Server({"Thailand": LOW_CONFIDENCE})
    plan = _plan(index, availability, {"service_phrase": "shots before my trip to Thailand"}, server)
    assert _types(plan) == {"appt_010"} and len(server.requests) == 1


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
    server = Server({"the tall one": {"prov_046": 0.97, "prov_000": 0.03}})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "cardiology consultation",
                                       "provider_phrase": "Dr. Chen, the tall one"}, server)
    assert _provs(plan) == {"prov_046"}
    assert set(server.requests[0]["questions"]["pick"]["criteria"]) == {"prov_000", "prov_046"}


# p(woman) as JEV answered the round 3 dev requests, and names it is sure of.
ROUND3_GENDERS = {"Jennifer": 0.85, "Maria": 0.87, "Daniel": 0.17, "David": 0.09, "Emily": 0.83}
SURE_GENDERS = {"Jennifer": 0.97, "Maria": 0.95, "Daniel": 0.03, "David": 0.02, "Emily": 0.96, "Carlos": 0.04}


def _talk(index, availability, server, *updates):
    client = _client(server) if server else None
    hooks = {"disambiguator": JevTypeDisambiguator(index, client, GATE),
             "chooser": JevProviderChooser(index, client, GATE)} if client else {}
    req, plans = Request(), []
    for u in updates:
        req = merge(req, Update.from_args(u))
        plans.append(resolve(index, req, availability, **hooks))
        req = plans[-1].req
    return plans


@pytest.mark.parametrize("p, gender", [(0.9, "female"), (0.89, None), (0.5, None), (0.11, None), (0.1, "male"),
                                       (None, None)])
def test_gender_counts_only_when_the_model_is_sure(p, gender):
    assert gender_of(p) == gender


def test_gender_narrows_to_everyone_it_fits(index, availability):
    server = Server(nouls=SURE_GENDERS)
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "pulmonology consultation",
                                       "provider_phrase": "Dr. Nguyen, the lady doctor"}, server)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", ("prov_023", "prov_024"))
    assert [set(r["questions"]) for r in server.requests] == [{"prov_023", "prov_024", "prov_028"}]


def test_a_name_the_model_is_not_sure_of_is_never_ruled_out(index, availability):
    server = Server(nouls=ROUND3_GENDERS)
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "pulmonology consultation",
                                       "provider_phrase": "Dr. Nguyen, the lady doctor"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("prov_023", "prov_024", "prov_028"))


@pytest.mark.parametrize("genders", [SURE_GENDERS, ROUND3_GENDERS])
def test_gender_alone_never_books_it_confirms_the_doctor_by_name(index, availability, genders):
    """h2-p01. With the round 3 scores David (0.09) is ruled out and Emily (0.83) is unknown."""
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "cardiology consultation",
                                       "provider_phrase": "Dr. Chen, the woman"}, Server(nouls=genders))
    assert (plan.status, plan.ask.field, plan.ask.options, plan.say) == (
        "ask", "provider", ("prov_046",), "Do you mean Dr. Emily Chen?")


def test_catalog_facts_narrow_before_gender(index, availability):
    server = Server(nouls=SURE_GENDERS)
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "pulmonology consultation",
                                       "provider_phrase": "Dr. Nguyen, he speaks Spanish"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("prov_028",))
    assert [set(r["questions"]) for r in server.requests] == [{"prov_023", "prov_028"}]


@pytest.mark.parametrize("daniel, options", [(0.5, ("prov_023", "prov_028")), (0.04, ("prov_023",))])
def test_a_wrong_or_unsure_gender_answer_never_books(index, availability, daniel, options):
    """Review round 3: a stub that wrongly calls Maria Nguyen male made "the woman" offer Jennifer."""
    server = Server(nouls={"Jennifer": 0.95, "Maria": 0.05, "Daniel": daniel})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "pulmonology consultation",
                                       "provider_phrase": "Dr. Nguyen, the woman"}, server)
    assert (plan.status, plan.ask.options) == ("ask", options)


@pytest.mark.parametrize("genders, options", [
    ({"Carlos": 0.03}, ("prov_002", "prov_003", "prov_008")),   # the facts say Carlos, the caller says "she"
    ({"Carlos": 0.5}, ("prov_008",)),                           # unknown: confirm the facts' doctor by name
    (None, ("prov_008",)),                                      # no model
])
def test_a_gender_that_the_facts_doctor_may_not_fit_asks(index, availability, genders, options):
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "annual physical",
                                       "provider_phrase": "Dr. Garcia, she speaks Spanish"},
                 Server(nouls=genders) if genders else None)
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "provider", options)


@pytest.mark.parametrize("phrase", ["Dr. Chen, my daughter's doctor, she is great",
                                    "Dr. Chen, the one who delivered her baby",
                                    "Dr. Chen, the women's health one"])
def test_words_about_someone_else_are_no_gender(index, availability, phrase):
    server = Server(nouls=SURE_GENDERS)
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "annual physical",
                                       "provider_phrase": phrase}, server)
    assert plan.status == "ask" and not any("noul" in str(r) for r in server.requests)


@pytest.mark.parametrize("answer, provider", [("yes", "prov_046"), ("yeah, that's her", "prov_046"),
                                              ("Dr. Emily Chen", "prov_046"), ("David Chen", "prov_000")])
def test_the_answer_to_a_confirmation_books_the_doctor_it_names(index, availability, answer, provider):
    first, second = _talk(index, availability, Server(nouls=SURE_GENDERS),
                          {**EXISTING_REF, "service_phrase": "cardiology consultation",
                           "provider_phrase": "Dr. Chen, the woman"},
                          {"provider_phrase": answer})
    assert first.ask.options == ("prov_046",)
    assert second.status == "offer" and _provs(second) == {provider}


@pytest.mark.parametrize("answer", ["no", "nope, not her", "the other one", "no, Lucas Chen"])
def test_a_no_to_a_confirmation_asks_about_the_others(index, availability, answer):
    first, second, third = _talk(index, availability, Server(nouls=SURE_GENDERS),
                                 {**EXISTING_REF, "service_phrase": "cardiology consultation",
                                  "provider_phrase": "Dr. Chen, the woman"},
                                 {"provider_phrase": answer}, {"provider_phrase": "yes"})
    assert first.ask.options == ("prov_046",)
    assert (second.status, second.ask.options, second.say) == ("ask", ("prov_000",), "Do you mean Dr. David Chen?")
    assert third.status == "offer" and _provs(third) == {"prov_000"}


@pytest.mark.parametrize("outcome", [httpx.ReadTimeout, 503, {"Jennifer": 0.5, "Daniel": 0.6}])
def test_unknown_gender_rules_nobody_out(index, availability, outcome):
    server = Server(nouls=outcome) if isinstance(outcome, dict) else Server(script={"": outcome})
    plan = _plan(index, availability, {**EXISTING_REF, "service_phrase": "pulmonology consultation",
                                       "provider_phrase": "Dr. Nguyen, he speaks Spanish"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("prov_023", "prov_028"))


def test_catalog_facts_need_no_model(index, availability):
    server = Server()
    for phrase, expected in (("Dr. Garcia who speaks Spanish", {"prov_008"}),
                             ("Maria Garcia, the nurse practitioner", {"prov_003"}),
                             ("Dr. Chen, the pediatrician", {"prov_012"}),
                             ("Dr. Patel, the internal medicine doctor downtown", {"prov_045"})):
        plan = _plan(index, availability, {"is_new": False, "service_phrase": "annual physical"
                                           if "Patel" not in phrase else "sick visit", "provider_phrase": phrase}, server)
        assert _provs(plan) == expected, phrase
    assert server.requests == []


REVIEW_PROBES = [  # review round 3, finding 1: each committed to a doctor on a misread "fact"
    "Dr. Patel, the man",                           # "man" ~ Main St
    "Dr. Garcia, the man doctor",
    "Dr. Patel, the one my boy sees",               # "boy" ~ Mission Bay
    "Dr. Patel, the good one",                      # "good" ~ North Gate
    "Dr. Patel, the one in the back",               # "back" ~ North Beach
    "Dr. Garcia, not the one who speaks Spanish",   # negation ignored
    "Dr. Chen, the one my daughter recommended",    # kin word read as Pediatrics
    "Dr. Patel, my main doctor",                    # "main" said as no street
]


@pytest.mark.parametrize("phrase", REVIEW_PROBES)
@pytest.mark.parametrize("model", ["none", "unsure"])
def test_ordinary_words_negations_and_kin_words_are_no_provider_facts(index, availability, phrase, model):
    server = Server(default=None, script={"": LOW_CONFIDENCE}) if model == "unsure" else None
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "annual physical",
                                       "provider_phrase": phrase}, server)
    assert plan.status == "ask" and plan.ask.field.startswith("provider"), plan.say
    assert not any(n.startswith("provider facts") for n in plan.notes)


def test_a_negated_description_asks_without_the_model(index, availability):
    server = Server(default={"prov_008": 1.0})
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "annual physical",
                                       "provider_phrase": "Dr. Garcia, the one who isn't Spanish speaking"}, server)
    assert (plan.status, plan.ask.options) == ("ask", ("prov_002", "prov_003", "prov_008"))
    assert server.requests == []


@pytest.mark.parametrize("phrase, expected", [
    ("Dr. Patel, the one on Main Street", {"prov_042"}),   # 4874 Main St is Mission Bay
    ("Dr. Maria Garcia who sees kids", {"prov_002"}),      # a patient group names Pediatrics
])
def test_exact_site_names_streets_and_patient_groups_are_facts(index, availability, phrase, expected):
    plan = _plan(index, availability, {"is_new": False, "service_phrase": "annual physical", "provider_phrase": phrase})
    assert _provs(plan) == expected


def test_policy_decides_before_chooser(index, availability):
    server = Server(default={"prov_000": 1.0})
    plan = _plan(index, availability, {"is_new": True, "has_referral": True, "service_phrase": "cardiology consultation",
                                       "provider_phrase": "Dr. Chen, the guy"}, server)
    assert _provs(plan) == {"prov_046"}
    assert server.requests == []


def test_a_confirmed_doctor_with_pending_policy_asks(index, availability):
    first, second = _talk(index, availability, Server(nouls={"David": 0.04, "Emily": 0.96}),
                          {"has_referral": True, "service_phrase": "cardiology consultation",
                           "provider_phrase": "Dr. Chen, the guy"},
                          {"provider_phrase": "yes"})
    assert (first.status, first.ask.options) == ("ask", ("prov_000",))
    # David Chen is closed to new patients, so whether the caller is new decides it.
    assert (second.status, second.ask.field) == ("ask", "is_new")


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
            return Verdict(ask=((bad or candidate_ids)[0], sorted(self.ix.unoffered_types)[0]), called=True)
        return Verdict(act=(bad or candidate_ids)[len(phrase) % len(bad or candidate_ids)], called=True)

    def check_type(self, phrase, hint, first, rival):
        bad = [t for t in (first.act or first.ask[0], rival) if not self.ix.types[t].new_patients_allowed]
        if self.mode == "outside":
            return Verdict(act=sorted(self.ix.unoffered_types)[0], called=True)
        return Verdict(act=(bad or [rival])[0], called=True)

    def provider_genders(self, candidate_ids):
        return {p: 1.0 for p in candidate_ids}

    def pick_provider(self, phrase, type_id, candidate_ids):
        if self.mode == "outside":
            return Verdict(act="prov_000", called=True)
        if self.mode == "pair":
            return Verdict(ask=("prov_000", "prov_036"), called=True)
        closed = [p for p in candidate_ids if not self.ix.providers[p].accepting_new_patients]
        return Verdict(act=(closed or candidate_ids)[-1], called=True)


def test_a_check_that_settles_outside_the_options_asks(index, availability):
    class Stray:
        def pick_type(self, phrase, hint, candidate_ids):
            return Verdict(act="appt_018", top=(("appt_018", 0.99),), called=True)

        def check_type(self, phrase, hint, first, rival):
            return Verdict(act=sorted(index.unoffered_types)[0], called=True)

    plan = resolve(index, merge(Request(), Update.from_args({"is_new": False, "service_phrase": "the zorbly paperwork"})),
                   availability, disambiguator=Stray())
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", ("appt_018", "appt_019"))


def test_openai_has_no_gender_question_so_none_is_asked(index):
    from scheduling.openai_chooser import OpenAIChoiceClient

    assert JevProviderChooser(index, OpenAIChoiceClient("k")).provider_genders(["prov_000", "prov_046"]) is None


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
