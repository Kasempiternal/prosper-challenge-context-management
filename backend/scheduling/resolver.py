"""resolve(index, req, availability) -> Plan: the next move for the conversation.

Pure with respect to the request: it reads availability but never holds. The only moves are
offer (<=3 concrete times), ask (one question whose answer changes the valid set), refuse
(specific reason + nearest valid alternative) and confirm (a picked offer, re-checked).
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from . import templates as T
from .availability import Availability, Slot as TimeSlot
from .catalog_index import BookableRow, CatalogIndex
from .decision import DECLINE, Verdict
from .lexicon import TypeCandidate, match_types, unexplained_words
from .names import clue_words, match_locations, match_providers
from .policy import IssueKind, Rule, Violation, check, has_violation
from .request import WEEKDAY_NAMES, AltRef, OfferRef, PendingAsk, Request, Slot, TimePref
from .text import normalize

TYPE_TIE_GAP = 0.1
MAX_OPTIONS = 3
HANDOFF_AFTER_MISSES = 3
_REFUSAL_PRIORITY = (Rule.NEW_PATIENT_TYPE, Rule.REFERRAL, Rule.NEW_PATIENT_PROVIDER)


class TypeDisambiguator(Protocol):
    """Maps a free-text reason to an appointment type among `candidate_ids` (every offered type,
    or the options of the question being answered). Consulted only when the lexicon found nothing,
    found only a specialty default, or tied between confusables. A declined Verdict means: do
    what the resolver does without a model."""

    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict: ...


class ProviderChooser(Protocol):
    """Splits same-named, policy-valid providers using the caller's extra words. Consulted only
    when those words exist; a bare name is a catalog fact that only a question can settle."""

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict: ...


class NoDisambiguator:
    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict:
        return DECLINE

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return DECLINE


@dataclass(frozen=True)
class Ask:
    field: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class Offer:
    n: int
    type_id: str
    provider_id: str
    location_id: str
    start: datetime
    duration_min: int


@dataclass(frozen=True)
class Refusal:
    code: str
    type_id: str | None = None
    alternatives: tuple[tuple[str, str, str], ...] = ()
    alt_type_id: str | None = None

    def refs(self) -> tuple[AltRef, ...]:
        """What pick_offer n adopts: each suggested booking, or the suggested other visit type."""
        if self.alternatives:
            return tuple(AltRef(i + 1, *a) for i, a in enumerate(self.alternatives))
        return (AltRef(1, self.alt_type_id),) if self.alt_type_id else ()


@dataclass(frozen=True)
class Plan:
    status: str  # offer | ask | refuse | confirm
    say: str
    req: Request
    ask: Ask | None = None
    offers: tuple[Offer, ...] = ()
    refusal: Refusal | None = None
    confirm: Offer | None = None
    summary: str = ""
    notes: tuple[str, ...] = ()
    valid_rows: int = 0
    result: dict | None = None

    def tool_result(self, speak_direct: bool = False) -> dict:
        """With speak-direct the handler already sent `say` to TTS, so the LLM only needs the facts."""
        result = self.result or {}
        return {k: v for k, v in result.items() if k != "say"} if speak_direct else result


def resolve(index: CatalogIndex, req: Request, availability: Availability,
            disambiguator: TypeDisambiguator | None = None, chooser: ProviderChooser | None = None) -> Plan:
    return _Resolution(index, req, availability, disambiguator or NoDisambiguator(),
                       chooser or NoDisambiguator()).run()


class _Resolution:
    def __init__(self, index, req, availability, disambiguator, chooser):
        self.ix: CatalogIndex = index
        self.req: Request = req
        self.av: Availability = availability
        self.dis: TypeDisambiguator = disambiguator
        self.chooser: ProviderChooser = chooser
        self.type_consulted = False
        self.patient = req.patient
        self.slots: dict[str, Slot] = {"service": req.service, "provider": req.provider, "location": req.location}
        self.notes: list[str] = []
        self.preface = ""
        self.scores: dict[str, float] = {}
        self.valid_rows = 0

    # ---- entry ---------------------------------------------------------------------------

    def run(self) -> Plan:
        if self.req.pick is not None:
            plan = self._picked()
            if plan:
                return plan
        return self._search()

    def _picked(self) -> Plan | None:
        offer = next((o for o in self.req.offered if o.n == self.req.pick), None)
        if offer is None:
            alt = next((a for a in self.req.alternatives if a.n == self.req.pick), None)
            if alt is not None:
                self._adopt(alt)
            else:
                self.notes.append(f"pick {self.req.pick} matches no current offer")
            return None
        if not self._fits_change(offer):
            self.notes.append(f"pick {offer.n} does not fit this turn's change")
            self.preface = "That time doesn't match what you just asked for, so I looked again. "
            return None
        row = self.ix.row(offer.type_id, offer.provider_id, offer.location_id)
        issues = check(row, self.patient) if row else []
        if row is None or has_violation(issues):
            self.notes.append("picked offer no longer passes policy")
            self.preface = "That one won't work after all. "
            return None
        if issues:
            return self._ask(issues[0].field, context=row.type.id, keep_pick=True)
        chosen = Offer(offer.n, offer.type_id, offer.provider_id, offer.location_id,
                       datetime.fromisoformat(offer.start), offer.duration_min)
        if not self.av.is_open(TimeSlot(chosen.type_id, chosen.provider_id, chosen.location_id,
                                        chosen.start, chosen.duration_min)):
            self.notes.append("picked time was taken")
            self.preface = "Sorry, that time was just taken. "
            return None
        say = T.say_confirm(self.ix, chosen.type_id, chosen.provider_id, chosen.location_id, chosen.start, self.av.now)
        return self._finish("confirm", say, confirm=chosen, keep_pick=True)

    def _adopt(self, alt: AltRef) -> None:
        """The caller took a suggested alternative: pin the slots to it and search again."""
        turn = self.req.turn
        self.notes.append(f"adopted alternative {alt.n}")
        self.slots["service"] = Slot(heard=self.ix.types[alt.type_id].name, exact=True, within=(alt.type_id,), turn=turn)
        if alt.provider_id:
            self.slots["provider"] = Slot(heard=self.ix.providers[alt.provider_id].name, within=(alt.provider_id,),
                                          turn=turn)
        if alt.location_id:
            self.slots["location"] = Slot(heard=self.ix.locations[alt.location_id].short_name,
                                          within=(alt.location_id,), turn=turn)

    def _fits_change(self, offer: OfferRef) -> bool:
        """A pick that arrives with a change ("the Tuesday one, at Downtown") stands only if the
        picked offer satisfies what changed."""
        changed = set(self.req.changed)
        if "service" in changed:
            svc = self._service_candidates() or []
            top = svc[0].score if svc else 0.0
            if offer.type_id not in {c.type_id for c in svc if c.score >= top - TYPE_TIE_GAP}:
                return False
        for name, picked in (("provider", offer.provider_id), ("location", offer.location_id)):
            if name in changed:
                ids = self._name_candidates(name)
                if ids is not None and picked not in ids:
                    return False
        if "time_pref" in changed and not _time_fits(datetime.fromisoformat(offer.start), self.req.time_pref):
            return False
        for name, picked in (("service", offer.type_id), ("provider", offer.provider_id),
                             ("location", offer.location_id)):
            if name in changed and self.slots[name].given:
                self.slots[name] = replace(self.slots[name], resolved_id=picked)
        return True

    # ---- main search ---------------------------------------------------------------------

    def _search(self, forced: list[TypeCandidate] | None = None) -> Plan:
        svc = forced or self._service_candidates()
        if svc is not None and not svc:
            svc = self._consult_types()
            if not svc:
                return self._miss("service")

        type_ids: list[str]
        if svc is None:
            type_ids = sorted(t for t in self.ix.types if t not in self.ix.unoffered_types)
        else:
            top = svc[0].score
            tier = [c for c in svc if c.score >= top - TYPE_TIE_GAP]
            offered = [c for c in tier if c.type_id not in self.ix.unoffered_types]
            if not offered:
                spec = self.ix.types[tier[0].type_id].specialty
                return self._refuse("not_offered", type_id=tier[0].type_id, specialty=spec)
            if all(c.via == "specialty" for c in offered):
                # "lung test" only reached Pulmonology's default type; the model may know better.
                offered = self._consult_types() or offered
            type_ids = [c.type_id for c in offered]
            self.scores = {c.type_id: c.score for c in offered}
            if len(type_ids) == 1:
                self.slots["service"] = replace(self.slots["service"], resolved_id=type_ids[0])

        provider_ids = self._name_candidates("provider")
        if provider_ids == []:
            return self._miss("provider")
        location_ids = self._name_candidates("location")
        if location_ids == []:
            return self._miss("location")

        rows = [r for tid in type_ids for r in self.ix.rows_by_type[tid]]
        rows_p = rows if provider_ids is None else [r for r in rows if r.provider.id in provider_ids]
        if provider_ids is not None and not rows_p:
            if self._newer("service", "provider"):
                self.preface = f"I can't book that with {self._who(provider_ids)}. "
                self.notes.append("dropped provider: does not offer the new service")
                self.slots["provider"] = Slot(turn=self.req.turn)
                provider_ids, rows_p = None, rows
            else:
                alts = self._alternatives(rows, location_ids)
                return self._refuse("provider_type", type_id=self._best_type(rows), who=self._who(provider_ids),
                                    alternatives=alts)

        rows_pl = rows_p if location_ids is None else [r for r in rows_p if r.location.id in location_ids]
        if location_ids is not None and not rows_pl:
            if self._newer("service", "location") or self._newer("provider", "location"):
                self.preface += f"That's not available at {self._where(location_ids)}. "
                self.notes.append("dropped location: does not fit the newer choice")
                self.slots["location"] = Slot(turn=self.req.turn)
                location_ids, rows_pl = None, rows_p
            else:
                return self._refuse_location(rows_p, provider_ids, location_ids)

        ok, pending, bad = [], [], []
        issues: dict[tuple, list[Violation]] = {}
        for r in rows_pl:
            found = check(r, self.patient)
            issues[r.key] = found
            (ok if not found else bad if has_violation(found) else pending).append(r)
        self.valid_rows = len(ok) + len(pending)
        if bad:
            self.notes.append(f"policy removed {len(bad)} rows: "
                              + ", ".join(sorted({f'{r.provider.id}:{v.rule.value}' for r in bad
                                                  for v in issues[r.key] if v.kind is IssueKind.VIOLATION}))[:200])
        if not ok and not pending:
            return self._refuse_policy(bad, issues, rows, location_ids)

        live = ok + pending
        live_types = sorted({r.type.id for r in live}, key=lambda t: (-self.scores.get(t, 0.0), t))
        if len(live_types) > 1:
            chosen = self._choose_type(live_types, svc is not None)
            if chosen is None:
                return self._ask_type(live_types, svc is not None)
            if chosen not in live_types:
                # "checkups while I'm expecting" tied Annual Physical / Wellness lexically, but the
                # model is confident it is a prenatal visit: start over from that type.
                pick = [TypeCandidate(chosen, 1.0, "model")]
                self.slots["service"] = replace(self.slots["service"], candidates=((chosen, 1.0),))
                return self._search(pick)
            ok = [r for r in ok if r.type.id == chosen]
            pending = [r for r in pending if r.type.id == chosen]
            live = ok + pending
        type_id = live[0].type.id
        self.slots["service"] = replace(self.slots["service"], resolved_id=type_id)

        if not ok:
            return self._ask_needed(pending, issues, type_id)

        if provider_ids is not None:
            provs = sorted({r.provider.id for r in live})
            if len(provs) > 1:
                verdict = self._consult_provider(provs, type_id)
                if verdict.pair:
                    return self._ask("provider", options=tuple(sorted(verdict.pair)))
                if verdict.act not in provs:
                    return self._ask_provider(provs)
                provs = [verdict.act]
                ok = [r for r in ok if r.provider.id == verdict.act]
                live = [r for r in live if r.provider.id == verdict.act]
                if not ok:
                    return self._ask_needed(live, issues, type_id)
            self.slots["provider"] = replace(self.slots["provider"], resolved_id=provs[0])

        if location_ids is not None:
            locs = sorted({r.location.id for r in live})
            if len(locs) > 1:
                return self._ask("location", options=tuple(locs)) if len(locs) <= MAX_OPTIONS else self._ask("location_open")
            self.slots["location"] = replace(self.slots["location"], resolved_id=locs[0])

        found = self.av.find(ok, self.req.time_pref, MAX_OPTIONS)
        if not found:
            return self._refuse("no_availability", type_id=type_id)
        names = [self.ix.providers[s.provider_id].name for s in found]
        order = list(dict.fromkeys(names))
        found = [s for _, s in sorted(zip(names, found), key=lambda ns: (order.index(ns[0]), ns[1].start))]
        offers = tuple(Offer(i + 1, s.type_id, s.provider_id, s.location_id, s.start, s.duration_min)
                       for i, s in enumerate(found))
        say = T.say_offers(self.ix, type_id, [(o.provider_id, o.location_id, o.start) for o in offers],
                           self.av.now, preface=self.preface)
        return self._finish("offer", say, offers=offers)

    # ---- candidates ----------------------------------------------------------------------

    def _service_candidates(self) -> list[TypeCandidate] | None:
        s = self.slots["service"]
        if not s.given:
            return None
        cands: list[TypeCandidate] = []
        if s.exact and s.heard:
            norm = normalize(s.heard)
            cands = [TypeCandidate(t.id, 1.0, "name") for t in self.ix.types.values() if normalize(t.name) == norm]
        if not cands:
            cands = match_types(self.ix, s.heard, s.hint)
        if s.within:
            inside = [c for c in cands if c.type_id in s.within]
            cands = inside or cands
        if cands:
            self.slots["service"] = replace(s, asks=0, candidates=tuple((c.type_id, c.score) for c in cands[:5]))
        return cands

    def _name_candidates(self, slot_name: str) -> list[str] | None:
        s = self.slots[slot_name]
        if not s.heard:
            return None
        matcher = match_providers if slot_name == "provider" else match_locations
        cands = matcher(self.ix, s.heard, s.within or None)
        if cands:
            self.slots[slot_name] = replace(s, asks=0, candidates=tuple((c.id, c.score) for c in cands))
        return [c.id for c in cands]

    def _newer(self, a: str, b: str) -> bool:
        return self.slots[a].given and self.slots[a].turn > self.slots[b].turn

    def _best_type(self, rows: list[BookableRow]) -> str | None:
        ids = {r.type.id for r in rows}
        return min(ids, key=lambda t: (-self.scores.get(t, 0.0), t)) if ids else None

    # ---- type choice ---------------------------------------------------------------------

    def _consult_types(self) -> list[TypeCandidate] | None:
        """Ask the type disambiguator over every offered type (or the options the caller is
        answering). Unoffered types are never candidates: "we don't offer that" stays lexical."""
        s = self.slots["service"]
        if not s.heard or self.type_consulted:
            return None
        self.type_consulted = True
        pool = [t for t in (s.within or sorted(self.ix.types)) if t not in self.ix.unoffered_types]
        verdict = self.dis.pick_type(s.heard, s.hint, pool)
        self.notes.append(f"type disambiguator: {verdict.describe()}")
        ids = [verdict.act] if verdict.act else list(verdict.pair or ())
        if not ids or not set(ids) <= set(pool):
            return None
        cands = [TypeCandidate(t, 1.0, "model") for t in ids]
        self.slots["service"] = replace(s, asks=0, candidates=tuple((c.type_id, c.score) for c in cands))
        return cands

    def _choose_type(self, live_types: list[str], has_service: bool) -> str | None:
        if not has_service or self.type_consulted:
            return None
        a, b = live_types[0], live_types[1]
        tied = abs(self.scores.get(a, 0) - self.scores.get(b, 0)) <= TYPE_TIE_GAP
        tie = [t for t in live_types if abs(self.scores.get(a, 0) - self.scores.get(t, 0)) <= TYPE_TIE_GAP]
        if tied and b in self.ix.confusables[a] and unexplained_words(self.ix, self.slots["service"].heard, tie):
            # Words beyond the tied names/aliases are the only evidence a model could use; without
            # them its answer is a prior ("MRI" -> brain 0.89), so the caller is asked instead.
            s = self.slots["service"]
            self.type_consulted = True
            pool = sorted(t for t in self.ix.types if t not in self.ix.unoffered_types)
            verdict = self.dis.pick_type(s.heard or "", s.hint, pool)
            self.notes.append(f"type disambiguator: {verdict.describe()}")
            if verdict.act in pool:
                return verdict.act
        return None

    def _consult_provider(self, provs: list[str], type_id: str) -> Verdict:
        heard = self.slots["provider"].heard or ""
        clue = clue_words(self.ix, heard, provs)
        if not clue:
            return DECLINE
        verdict = self.chooser.pick_provider(heard, type_id, provs)
        self.notes.append(f"provider chooser on {list(clue)}: {verdict.describe()}")
        if verdict.pair and not set(verdict.pair) <= set(provs):
            return DECLINE
        return verdict

    def _ask_needed(self, pending: list[BookableRow], issues: dict[tuple, list[Violation]], type_id: str) -> Plan:
        fields = Counter(v.field for r in pending for v in issues[r.key] if v.kind is IssueKind.NEEDS_INFO)
        field = max(("is_new", "has_referral"), key=lambda f: fields.get(f, 0))
        return self._ask(field, context=type_id)

    def _ask_type(self, live_types: list[str], has_service: bool) -> Plan:
        if not has_service or len(live_types) > MAX_OPTIONS:
            return self._ask("service_open")
        return self._ask("service", options=tuple(live_types))

    def _ask_provider(self, provs: list[str]) -> Plan:
        if len(provs) <= MAX_OPTIONS:
            return self._ask("provider", options=tuple(provs))
        surnames = {self.ix.providers[p].last_name for p in provs}
        context = f"Dr. {surnames.pop()}" if len(surnames) == 1 else None
        return self._ask("provider_first_name", options=tuple(provs), context=context)

    # ---- refusals ------------------------------------------------------------------------

    def _alternatives(self, rows: list[BookableRow], location_ids: list[str] | None,
                      exclude_providers: set[str] = frozenset()) -> tuple[tuple[str, str, str], ...]:
        """Nearest valid options: same service, other providers/sites, that pass policy for this
        patient now (needs-info rows count, the caller can still answer). Same-site first."""
        valid = [r for r in rows if r.provider.id not in exclude_providers
                 and not has_violation(check(r, self.patient))]
        if location_ids:
            valid.sort(key=lambda r: (r.location.id not in location_ids, bool(check(r, self.patient)), r.key))
        else:
            valid.sort(key=lambda r: (bool(check(r, self.patient)), r.key))
        out, seen = [], set()
        for r in valid:
            # Two "Dr. Maria Garcia" alternatives in one sentence would be unanswerable.
            if r.provider.name in seen:
                continue
            seen.add(r.provider.name)
            out.append(r.key)
            if len(out) == 2:
                break
        return tuple(out)

    def _refuse_location(self, rows_p, provider_ids, location_ids) -> Plan:
        type_id = self._best_type(rows_p)
        alts = self._alternatives(rows_p, None)
        loc = location_ids[0] if len(location_ids) == 1 else None
        if provider_ids is not None and not any(set(self.ix.providers[p].location_ids) & set(location_ids)
                                                for p in provider_ids):
            single = provider_ids[0] if len(provider_ids) == 1 else None
            return self._refuse("provider_location", type_id=type_id, who=self._who(provider_ids),
                                provider_id=single, location_id=loc, alternatives=alts)
        return self._refuse("location_type", type_id=type_id, location_id=loc, alternatives=alts)

    def _refuse_policy(self, bad, issues, rows_all, location_ids) -> Plan:
        rules_per_row = [{v.rule for v in issues[r.key] if v.kind is IssueKind.VIOLATION} for r in bad]
        rule = next((ru for ru in _REFUSAL_PRIORITY if all(ru in s for s in rules_per_row)), None)
        if rule is None:
            rule = Counter(ru for s in rules_per_row for ru in s).most_common(1)[0][0]
        type_id = self._best_type(bad)
        t = self.ix.types[type_id]
        if rule is Rule.NEW_PATIENT_TYPE:
            alt_type = self.ix.specialty_default.get(t.specialty)
            if alt_type == type_id or not any(not has_violation(check(r, self.patient))
                                              for r in self.ix.rows_by_type.get(alt_type, ())):
                alt_type = None
            return self._refuse("new_patient_type", type_id=type_id, alt_type_id=alt_type,
                                needs_referral=t.requires_referral and self.patient.has_referral is not True)
        if rule is Rule.REFERRAL:
            return self._refuse("referral", type_id=type_id)
        if rule is Rule.NEW_PATIENT_PROVIDER:
            named = sorted({r.provider.id for r in bad})
            same_type = [r for r in rows_all if r.type.id == type_id]
            alts = self._alternatives(same_type, location_ids, exclude_providers=set(named))
            return self._refuse("new_patient_provider", type_id=type_id, who=self._who(named), alternatives=alts)
        return self._refuse("handoff", type_id=type_id)

    # ---- moves ---------------------------------------------------------------------------

    def _who(self, provider_ids: list[str]) -> str:
        provs = [self.ix.providers[p] for p in provider_ids]
        if len({p.name for p in provs}) == 1:
            return provs[0].name
        surnames = {p.last_name for p in provs}
        return f"Dr. {surnames.pop()}" if len(surnames) == 1 else "that doctor"

    def _where(self, location_ids: list[str]) -> str:
        return T.join_or([self.ix.locations[l].short_name for l in location_ids])

    def _miss(self, slot_name: str) -> Plan:
        s = self.slots[slot_name]
        asks = s.asks + 1
        self.slots[slot_name] = replace(s, asks=asks, candidates=())
        if asks >= HANDOFF_AFTER_MISSES:
            return self._refuse("handoff")
        field = {"service": "service_open",
                 "provider": "provider_retry" if asks == 1 else "provider_spelling",
                 "location": "location_retry"}[slot_name]
        return self._ask(field)

    def _ask(self, field: str, options: tuple[str, ...] = (), context: str | None = None,
             keep_pick: bool = False) -> Plan:
        say = self.preface + T.say_ask(self.ix, field, list(options), context)
        return self._finish("ask", say, ask=Ask(field, options), keep_pick=keep_pick)

    def _refuse(self, code: str, **kw) -> Plan:
        say = self.preface + T.say_refuse(self.ix, code, **kw)
        refusal = Refusal(code, kw.get("type_id"), tuple(kw.get("alternatives", ())), kw.get("alt_type_id"))
        return self._finish("refuse", say, refusal=refusal)

    def _finish(self, status: str, say: str, ask: Ask | None = None, offers: tuple[Offer, ...] = (),
                refusal: Refusal | None = None, confirm: Offer | None = None, keep_pick: bool = False) -> Plan:
        pending_field = {"provider_retry": "provider", "provider_spelling": "provider",
                         "provider_first_name": "provider", "location_retry": "location",
                         "location_open": "location", "service_open": "service"}
        new_req = replace(
            self.req,
            service=self.slots["service"], provider=self.slots["provider"], location=self.slots["location"],
            offered=tuple(OfferRef(o.n, o.type_id, o.provider_id, o.location_id, o.start.isoformat(), o.duration_min)
                          for o in offers) if status == "offer" else (self.req.offered if keep_pick else ()),
            alternatives=refusal.refs() if refusal else (),
            pick=self.req.pick if keep_pick else None,
            changed=(),
            pending_ask=PendingAsk(pending_field.get(ask.field, ask.field), ask.options) if ask else None,
        )
        summary = _summary(self.ix, new_req, status, ask)
        result = _tool_result(self.ix, status, say, ask, offers, new_req, confirm)
        if new_req.alternatives:
            result["alternatives"] = _alternative_labels(self.ix, new_req.alternatives)
        return Plan(status=status, say=say, req=new_req, ask=ask, offers=offers, refusal=refusal, confirm=confirm,
                    summary=summary, notes=tuple(self.notes), valid_rows=self.valid_rows, result=result)


# ---- compact views for the LLM --------------------------------------------------------------

def _known(ix: CatalogIndex, req: Request) -> dict:
    def flag(v):
        return "unknown" if v is None else ("yes" if v else "no")
    out = {"new_patient": flag(req.patient.is_new), "referral": flag(req.patient.has_referral)}
    if req.service.resolved_id:
        out["visit"] = ix.types[req.service.resolved_id].name
    elif req.service.heard:
        out["visit_heard"] = req.service.heard
    if req.provider.resolved_id:
        out["doctor"] = ix.providers[req.provider.resolved_id].name
    elif req.provider.heard:
        out["doctor_heard"] = req.provider.heard
    if req.location.resolved_id:
        out["location"] = ix.locations[req.location.resolved_id].short_name
    elif req.location.heard:
        out["location_heard"] = req.location.heard
    tp = req.time_pref
    if tp.days or tp.part_of_day or tp.not_before:
        out["time"] = " ".join(filter(None, [",".join(tp.days), tp.part_of_day, tp.not_before and f"from {tp.not_before}"]))
    return out


def _time_fits(start: datetime, tp: TimePref) -> bool:
    if tp.days and WEEKDAY_NAMES[start.weekday()] not in tp.days:
        return False
    if tp.part_of_day and (start.hour < 12) != (tp.part_of_day == "morning"):
        return False
    return not (tp.not_before and start.date().isoformat() < tp.not_before)


def _alternative_labels(ix: CatalogIndex, alts: tuple[AltRef, ...]) -> list[str]:
    return [" ".join(filter(None, [str(a.n), ix.types[a.type_id].name,
                                   a.provider_id and ix.providers[a.provider_id].name,
                                   a.location_id and ix.locations[a.location_id].short_name])) for a in alts]


def _option_label(ix: CatalogIndex, field: str, option: str) -> str:
    if field == "service":
        return ix.types[option].name
    if field.startswith("provider"):
        return ix.providers[option].name
    if field.startswith("location"):
        return ix.locations[option].short_name
    return option


def _tool_result(ix, status, say, ask, offers, req, confirm) -> dict:
    out: dict = {"status": status, "say": say}
    if ask:
        out["ask"] = ask.field if not ask.options else {
            "field": ask.field, "options": [_option_label(ix, ask.field, o) for o in ask.options]}
    if offers:
        out["offers"] = [f"{o.n} {T.short_when(o.start)} {ix.locations[o.location_id].short_name} "
                         f"{ix.providers[o.provider_id].name}" for o in offers]
    if confirm:
        out["held_candidate"] = confirm.n
    # Unknown flags are implied by absence; dropping them keeps the result under ~120 tokens.
    out["known"] = {k: v for k, v in _known(ix, req).items() if v != "unknown"}
    return out


def _summary(ix: CatalogIndex, req: Request, status: str, ask: Ask | None) -> str:
    k = _known(ix, req)
    parts = [f"{key.replace('_', ' ')}: {val}" for key, val in k.items()]
    if req.offered:
        parts.append("offered: " + "; ".join(
            f"{o.n}) {T.short_when(datetime.fromisoformat(o.start))} {ix.locations[o.location_id].short_name} "
            f"{ix.providers[o.provider_id].name}" for o in req.offered))
    if req.alternatives:
        parts.append("alternatives: " + "; ".join(_alternative_labels(ix, req.alternatives)))
    nxt = {"offer": "caller picks a time", "ask": f"waiting for answer about {ask.field if ask else ''}",
           "refuse": "caller may pick an alternative by number or ask for something else", "confirm": "caller confirms booking"}[status]
    parts.append(f"next: {nxt}")
    return "; ".join(parts) + "."


def plan_json(plan: Plan, speak_direct: bool = False) -> str:
    return json.dumps(plan.tool_result(speak_direct), separators=(",", ":"))
