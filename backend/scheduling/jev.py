"""JEV (Command Code decision model) client and the choice-question hooks built on it.

The hooks ask choice questions, which OpenAIChoiceClient answers too (scheduling.choosers wires
them to either client); only JEV answers yes/no questions (provider gender).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from loguru import logger

from .catalog_index import CatalogIndex
from .decision import FAILED, Check, CheckGate, Gate, Verdict
from .lexicon import SHORTLIST_SIZE, ranked_types, type_shortlist
from .model_client import CachedModelClient, ModelCall, cache_key, ssl_context
from .per_index import per_index

URL = "https://api.commandcode.ai/provider/v1/systemone"
MODEL = "typesafe/jev"
USD_PER_INPUT_TOKEN = 0.04 / 1_000_000
_QUESTION = "pick"
MAX_CHOICE_OPTIONS = 255  # JEV answers HTTP 400 "TypeSafe Choice questions support at most 255 options"


@dataclass(frozen=True)
class JevAnswer:
    choice: str
    confidence: float
    probabilities: dict[str, float]


class JevClient(CachedModelClient):
    """The shared ladder (scheduling.model_client) over JEV's HTTP API. The offline eval passes a
    longer timeout and one retry on connect errors."""

    provider = "jev"
    key_env = "CMD_API_KEY"
    _retry_on = (httpx.ConnectError,)
    _give_up_on = (httpx.HTTPError,)

    def __init__(self, api_key: str | None, *, warmup_timeout_s: float = 15.0,
                 transport: httpx.BaseTransport | None = None, **kw):
        super().__init__(api_key, **kw)
        self.warmup_timeout_s = warmup_timeout_s
        self._http = httpx.Client(transport=transport, verify=ssl_context(),
                                  headers={"Authorization": f"Bearer {api_key}"} if api_key else {})

    def choice(self, state: str, instructions: str, criteria: dict[str, str],
               ranking: list[str] = (), purpose: str = "") -> JevAnswer | None:
        """ranking: option ids best first; decides which options survive the MAX_CHOICE_OPTIONS cap."""
        criteria = cap_options(criteria, ranking)
        answers = self._fetch(_choice_body(state, instructions, criteria), purpose, _read)
        return _parse(answers[_QUESTION], criteria) if answers else None

    def prefetch_choice(self, state: str, instructions: str, criteria: dict[str, str],
                        ranking: list[str] = ()) -> None:
        """A later choice() of the same question waits for this request. The cache key ignores the
        option order, so either order of a pair is the same request."""
        self.prefetch(_choice_body(state, instructions, cap_options(criteria, ranking)))

    def nouls(self, state: str, questions: dict[str, str], purpose: str = "") -> dict[str, float] | None:
        """Several yes/no questions about one state in one request: name -> probability of yes."""
        body = {"model": MODEL, "state": state,
                "questions": {name: {"type": "noul", "instructions": text} for name, text in questions.items()}}
        answers = self._fetch(body, purpose, _read)
        try:
            return {name: float(answers[name]["noul"]) for name in questions} if answers else None
        except (KeyError, TypeError, ValueError):
            return None

    def warm_up(self, index: CatalogIndex) -> ModelCall | None:
        """Pays the server-side cold path with a request shaped like the ones a call will make."""
        return self.warm_up_on(warm_up_criteria(index))

    def warm_up_on(self, criteria: dict[str, str]) -> ModelCall | None:
        """Always goes to the network and is never cached, so it measures and warms the real path."""
        body = _choice_body("A patient calling a multi-specialty clinic said they want: 'an appointment'",
                            "Which appointment type is the caller asking for?", cap_options(criteria))
        entry, ms = self._post(body, timeout_s=self.warmup_timeout_s, use_budget=False)
        call = ModelCall(cache_key(body), "live" if entry else "failed", ms, entry["input_tokens"] if entry else 0,
                         purpose="warmup", p=_read(entry)[1] if entry else None,
                         usd=self._usd(entry) if entry else 0.0)
        self.calls.append(call)
        return call if entry else None

    def close(self) -> None:
        self._http.close()

    # ---- the request -----------------------------------------------------------------------

    def _send(self, body: dict, timeout: float) -> httpx.Response:
        return self._http.post(URL, json=body, timeout=timeout)

    def _entry(self, body: dict, response: httpx.Response, ms: float) -> dict | None:
        try:
            data = response.json() if response.status_code == 200 else None
            answers = {name: data["answers"][name] for name in body["questions"]} if data else None
            tokens = int(data.get("usage", {}).get("input_tokens", 0)) if data else 0
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        if answers is None:
            return None
        # A lone "pick" keeps the original single-answer form, so earlier cache entries stay valid.
        return {"state": body["state"], "input_tokens": tokens, "latency_ms": ms,
                **({"answer": answers[_QUESTION]} if list(answers) == [_QUESTION] else {"answers": answers})}

    def _usd(self, entry: dict) -> float:
        return entry["input_tokens"] * USD_PER_INPUT_TOKEN


def _answers(entry: dict) -> dict:
    return entry["answers"] if "answers" in entry else {_QUESTION: entry["answer"]}


def _read(entry: dict) -> tuple[dict, float | None]:
    answers = _answers(entry)
    return answers, _top_p(answers)


def _top_p(answers: dict) -> float | None:
    """The top probability of a lone choice, or a lone yes/no's yes."""
    if len(answers) != 1:
        return None
    answer = next(iter(answers.values()))
    try:
        if "noul" in answer:
            return round(float(answer["noul"]), 3)
        return round(max(float(p) for p in answer["probabilities"].values()), 3)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


def _choice_body(state: str, instructions: str, criteria: dict[str, str]) -> dict:
    return {"model": MODEL, "state": state,
            "questions": {_QUESTION: {"type": "choice", "instructions": instructions, "criteria": criteria}}}


def cap_options(criteria: dict[str, str], ranking: list[str] = ()) -> dict[str, str]:
    """At most MAX_CHOICE_OPTIONS options: the ranked ones first, then in the given order. Kept
    options stay in the given order, so a request under the cap is unchanged."""
    if len(criteria) <= MAX_CHOICE_OPTIONS:
        return criteria
    keep = set(list(dict.fromkeys([k for k in ranking if k in criteria] + list(criteria)))[:MAX_CHOICE_OPTIONS])
    logger.warning(f"JEV choice has {len(criteria)} options; keeping the {MAX_CHOICE_OPTIONS} best ranked")
    return {k: v for k, v in criteria.items() if k in keep}


def _parse(answer: dict, criteria: dict[str, str]) -> JevAnswer | None:
    try:
        probs = {k: float(v) for k, v in answer["probabilities"].items() if k in criteria}
        return JevAnswer(str(answer["choice"]), float(answer.get("confidence", 0.0)), probs) if probs else None
    except (KeyError, TypeError, ValueError, AttributeError):
        return None


# ---- resolver hooks ----------------------------------------------------------------------

def type_criteria(index: CatalogIndex, type_ids: list[str]) -> dict[str, str]:
    out = {}
    for tid in sorted(type_ids):
        t = index.types[tid]
        out[tid] = f"{t.name} ({t.specialty}, {t.duration_min} min)"
    return out


def offered_type_criteria(index: CatalogIndex) -> dict[str, str]:
    return type_criteria(index, [t for t in index.types if t not in index.unoffered_types])


def warm_up_criteria(index: CatalogIndex) -> dict[str, str]:
    """A shortlist-sized option set, like the requests a call makes (a national catalog offers more
    types than one JEV choice question accepts)."""
    return type_criteria(index, type_shortlist(index, "an appointment", None)[:SHORTLIST_SIZE])


def provider_criteria(index: CatalogIndex, provider_ids: list[str]) -> dict[str, str]:
    out = {}
    for pid in sorted(provider_ids):
        p = index.providers[pid]
        sites = ", ".join(index.locations[l].short_name for l in p.location_ids)
        out[pid] = f"{p.name}, {p.title}, {p.specialty}; works at {sites}; speaks {', '.join(p.languages)}"
    return out


def type_check_criteria(index: CatalogIndex, type_ids: list[str]) -> dict[str, str]:
    """Two types side by side, with what callers call each (its aliases) and its booking rules."""
    aliases = _aliases_by_type(index)
    out = {}
    for tid in type_ids:
        t = index.types[tid]
        parts = [t.specialty]
        if aliases.get(tid):
            parts.append("for: " + ", ".join(aliases[tid][:6]))
        if t.requires_referral:
            parts.append("needs a referral")
        if not t.new_patients_allowed:
            parts.append("established patients only")
        out[tid] = f"{t.name} ({'; '.join(parts)})"
    return out


@per_index
def _aliases_by_type(index: CatalogIndex) -> dict[str, list[str]]:
    """Type id -> the aliases that mean it at least as much as any other type, shortest first."""
    out: dict[str, set[str]] = {}
    for a in index.aliases:
        top = max(w for _, w in a.weights)
        for tid, w in a.weights:
            if w >= top:
                out.setdefault(tid, set()).add(a.phrase)
    return {t: sorted(v, key=lambda s: (len(s), s)) for t, v in out.items()}


EITHER = "either"


def _said(phrase: str, hint: str | None) -> str:
    said = f"A patient calling a multi-specialty clinic said they want: '{phrase}'"
    return said + (f" (specialty mentioned: {hint})" if hint else "")


class JevTypeDisambiguator:
    def __init__(self, index: CatalogIndex, client, gate: Gate = Gate(), check_gate: CheckGate = CheckGate()):
        self.ix, self.client, self.gate, self.check_gate = index, client, gate, check_gate

    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict:
        ans = self.client.choice(*self._pick_question(phrase, hint, candidate_ids), purpose="type")
        return self.gate.decide(ans.probabilities) if ans else FAILED

    def prefetch_pick(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> None:
        self.client.prefetch_choice(*self._pick_question(phrase, hint, candidate_ids))

    def _pick_question(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> tuple[str, str, dict, list]:
        return (_said(phrase, hint), "Which appointment type is the caller asking for?",
                type_criteria(self.ix, candidate_ids), ranked_types(self.ix, phrase, hint))

    def check_type(self, phrase: str, hint: str | None, first: Verdict, rival: str) -> Verdict:
        """The choice's front-runner and its rival alone, plus "either": the third answer is what
        a choice question lacks, so words that fit both ("my yearly exam") stop looking like a
        confident pick. Settled by the check gate."""
        chosen = first.act or first.ask[0]
        ans = self.client.choice(*self._check_question(phrase, hint, chosen, rival), purpose="type check")
        p = ans.probabilities if ans else {}
        return self.check_gate.decide(first, rival, Check(p.get(chosen, 0.0), p.get(rival, 0.0), p.get(EITHER, 0.0))
                                      if ans else None)

    def prefetch_check(self, phrase: str, hint: str | None, pair: tuple[str, str]) -> None:
        self.client.prefetch_choice(*self._check_question(phrase, hint, *pair))

    def _check_question(self, phrase: str, hint: str | None, chosen: str, rival: str) -> tuple[str, str, dict]:
        criteria = {**type_check_criteria(self.ix, [chosen, rival]),
                    EITHER: "Either one: nothing the caller said tells these two visits apart"}
        return (_said(phrase, hint), "Which visit is the caller asking for? If their words fit both visits "
                "equally, answer 'either'.", criteria)


class ChoiceProviderChooser:
    """Same-named providers, split by a choice question over the caller's words."""

    def __init__(self, index: CatalogIndex, client, gate: Gate = Gate()):
        self.ix, self.client, self.gate = index, client, gate

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        visit = self.ix.types[type_id].name if type_id else "an appointment"
        said = (f"A patient calling a multi-specialty clinic to book {visit} asked for the provider: "
                f"'{phrase}'")
        ans = self.client.choice(said, "Which provider does the caller mean?",
                                 provider_criteria(self.ix, candidate_ids), purpose="provider")
        return self.gate.decide(ans.probabilities) if ans else FAILED

    def provider_genders(self, candidate_ids: list[str]) -> dict[str, float] | None:
        """One answer token has no calibrated yes (OpenAI): no question is asked."""
        return None


class JevProviderChooser(ChoiceProviderChooser):
    def provider_genders(self, candidate_ids: list[str]) -> dict[str, float] | None:
        """One yes/no question per provider, asked about first names only: a choice question
        over the caller's whole phrase put 0.89 on one of two women for "the lady doctor".
        Measured: Jennifer 0.82-0.85, Maria 0.87, Fatima 0.89, Daniel 0.13-0.17, David 0.09."""
        names = {pid: self.ix.providers[pid].name for pid in sorted(candidate_ids)}
        p_woman = self.client.nouls("Clinic providers: " + "; ".join(names.values()),
                                    {pid: f"Is {name} a woman?" for pid, name in names.items()},
                                    purpose="provider gender")
        return p_woman or {}


def site_criteria(index: CatalogIndex, location_ids: list[str]) -> dict[str, str]:
    out = {}
    for lid in sorted(location_ids):
        loc = index.locations[lid]
        where = ", ".join(filter(None, [loc.neighborhood, loc.address, loc.city]))
        caps = ", ".join(sorted(loc.capabilities)) or "general visits only"
        out[lid] = f"{loc.name}; {where}; on site: {caps}"
    return out


class JevSiteChooser:
    """Splits the clinics of an area search using the caller's description of the place."""

    def __init__(self, index: CatalogIndex, client, gate: Gate = Gate()):
        self.ix, self.client, self.gate = index, client, gate

    def pick_site(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        visit = self.ix.types[type_id].name if type_id else "an appointment"
        said = (f"A patient calling a multi-specialty clinic to book {visit} described the clinic location "
                f"they want: '{phrase}'")
        ans = self.client.choice(said, "Which clinic location does the caller mean?",
                                 site_criteria(self.ix, candidate_ids), purpose="site")
        return self.gate.decide(ans.probabilities) if ans else FAILED
