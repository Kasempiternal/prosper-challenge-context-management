"""resolve(index, req, availability) -> Plan: the next move for the conversation.

Pure with respect to the request: it reads availability but never holds. The only moves are
offer (<=3 concrete times), ask (one question whose answer changes the valid set), refuse
(specific reason + nearest valid alternative) and confirm (a picked offer, re-checked).
"""

from __future__ import annotations

import json
import math
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol

from . import templates as T
import jellyfish

from .availability import Availability, Slot as TimeSlot
from .catalog_index import BookableRow, CatalogIndex
from .decision import DECLINE, Verdict, gender_of, unanswered
from .geo import RADIUS_MI, Place, PlaceMatch, haversine, names_own_area, nearby, over_state_line, resolve_place
from .lexicon import (SHORTLIST_ABOVE, Doubt, TypeCandidate, fitting_kin, match_types, nearest_type,
                      pointed_default, stated_doubt, type_shortlist, types_named, umbrella, unexplained_words)
from .names import (STREET_TYPES, ProviderClues, hear_place, match_locations, match_providers, only_no,
                    read_confirmation, read_provider_clues)
from .policy import IssueKind, Rule, Violation, check, has_violation
from .request import WEEKDAY_NAMES, AltRef, OfferRef, PendingAsk, Request, Slot, TimePref
from .text import phonetic_keys, tokens

TYPE_TIE_GAP = 0.1
# A runner-up the first answer gave less than this is a long shot: 28 of 68 round 3 checks weighed
# one at <= 0.01 and none changed an outcome. The check weighs the choice's nearest neighbour instead.
RIVAL_MIN_P = 0.05
MAX_OPTIONS = 3
HANDOFF_AFTER_MISSES = 3
# An area search widens from the place's own radius to twice that, then to this, before refusing.
# No ring goes past it: an anchor whose own radius is wider (a state with no catalog city of its
# own) refuses with none_nearby at once, naming the nearest clinic that has the visit, in the state
# if it has one.
FINAL_RING_MI = 50.0
MAX_SITE_CHOICES = 20
_REFUSAL_PRIORITY = (Rule.NEW_PATIENT_TYPE, Rule.REFERRAL, Rule.NEW_PATIENT_PROVIDER)


class TypeDisambiguator(Protocol):
    """Maps a free-text reason to an appointment type among `candidate_ids` (every offered type,
    or the options of the question being answered). Consulted only when the lexicon found nothing,
    found only a specialty default, or left words of the phrase unexplained. A declined Verdict
    means: do what the resolver does without a model; a failed one: ask the caller what the model
    was asked. check_type weighs the choice's front-runner against `rival` once more and returns
    the settled verdict, or None if it has no such question."""

    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict: ...

    def check_type(self, phrase: str, hint: str | None, first: Verdict, rival: str) -> Verdict | None: ...

    def prefetch_check(self, phrase: str, hint: str | None, pair: tuple[str, str]) -> None:
        """The check of `pair` will most likely follow the next pick_type: start it now, so the two
        requests overlap. Changes no answer."""

    def prefetch_pick(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> None:
        """This pick_type will follow: start it now, so independent picks overlap. Changes no answer."""


class ProviderChooser(Protocol):
    """Splits same-named, policy-valid providers using the caller's extra words. Consulted only
    when those words exist; a bare name is a catalog fact that only a question can settle.
    pick_provider weighs words no catalog fact explains; provider_genders answers the one fact the
    caller may give that the catalog lacks: the probability that each provider is a woman, read
    off the first name (a provider left out has no answer), or None if the chooser has no such
    question."""

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict: ...

    def provider_genders(self, candidate_ids: list[str]) -> dict[str, float] | None: ...


class SiteChooser(Protocol):
    """Picks among the clinics of an area search using the caller's descriptive words ("the one on
    Lamar", "the big one by the river"). Consulted only when such words exist."""

    def pick_site(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict: ...


class NoDisambiguator:
    def pick_type(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> Verdict:
        return DECLINE

    def check_type(self, phrase: str, hint: str | None, first: Verdict, rival: str) -> Verdict | None:
        return None

    def prefetch_check(self, phrase: str, hint: str | None, pair: tuple[str, str]) -> None:
        pass

    def prefetch_pick(self, phrase: str, hint: str | None, candidate_ids: list[str]) -> None:
        pass

    def pick_provider(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return DECLINE

    def provider_genders(self, candidate_ids: list[str]) -> dict[str, float] | None:
        return None

    def pick_site(self, phrase: str, type_id: str | None, candidate_ids: list[str]) -> Verdict:
        return DECLINE


@dataclass(frozen=True)
class Area:
    """Where an area phrase ("Hyde Park", "Austin", "78701") was searched: every site within
    radius_mi of an anchor, plus a named city's own sites. Offers never leave it."""

    anchors: tuple[Place, ...]
    radius_mi: float
    location_ids: tuple[str, ...]  # nearest first


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
    consults: tuple[tuple[str, Verdict], ...] = ()  # (purpose, verdict) per model hook consulted
    valid_rows: int = 0
    result: dict | None = None
    area: Area | None = None

    def tool_result(self, speak_direct: bool = False) -> dict:
        """With speak-direct the handler already sent `say` to TTS, so the LLM only needs the facts."""
        result = self.result or {}
        return {k: v for k, v in result.items() if k != "say"} if speak_direct else result


def resolve(index: CatalogIndex, req: Request, availability: Availability,
            disambiguator: TypeDisambiguator | None = None, chooser: ProviderChooser | None = None,
            site_chooser: SiteChooser | None = None) -> Plan:
    return _Resolution(index, req, availability, disambiguator, chooser or NoDisambiguator(),
                       site_chooser or NoDisambiguator()).run()


class _Resolution:
    def __init__(self, index, req, availability, disambiguator, chooser, site_chooser):
        self.ix: CatalogIndex = index
        self.req: Request = req
        self.av: Availability = availability
        # With no type model, the words a model would weigh are not even looked for.
        self.type_model = disambiguator is not None
        self.dis: TypeDisambiguator = disambiguator or NoDisambiguator()
        self.chooser: ProviderChooser = chooser
        self.site_chooser: SiteChooser = site_chooser
        # A catalog without metros or coordinates (SF) never takes a geographic branch.
        self.geo = index.has_geo or index.multi_metro
        self.area: Area | None = None
        self.dist: dict[str, float] = {}   # location id -> miles from the caller's place
        self.site_choice = False           # the place left several sites a description could split
        self.widened = False               # nothing in the place's own radius: "the nearest is N miles away"
        self.place_memo: PlaceMatch | None = None
        self.type_consulted = False
        self.doubt = False                 # the caller said they do not know which visit: never commit
        self.provider_declined = False     # "no" to "Do you mean Dr. X?": whoever is left is asked about
        self.named_providers: list[str] = []      # every doctor the name matches
        self.provider_clues: ProviderClues | None = None
        self.described_sites: tuple[str, ...] = ()  # clinics named to describe the doctor
        self.described_ask: tuple[str, ...] = ()  # the facts' one doctor, unsettled by the gender said
        self.patient = req.patient
        self.slots: dict[str, Slot] = {"service": req.service, "provider": req.provider, "location": req.location}
        self.notes: list[str] = []
        self.consults: list[tuple[str, Verdict]] = []
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
                ids = self._place_ids() if name == "location" and self.geo else self._name_candidates(name)
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

    def _search(self) -> Plan:
        svc = self._service_candidates()
        if svc is not None:
            doubted = self._doubted(svc)
            if doubted == []:
                return self._ask("service_open")
            svc = doubted or svc
        if svc is not None and not svc:
            verdict = self._consult_types()
            if verdict.failed and verdict.ask:
                return self._ask_type(sorted(verdict.ask), True)
            svc = _model_candidates(verdict)
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
                instead = self._offered_instead(tier[0].type_id) if all(c.via == "specialty" for c in tier) else DECLINE
                if not instead.act:
                    return self._refuse_not_offered(tier[0].type_id)
                offered = _model_candidates(instead)
            verdict = DECLINE
            if all(c.via == "specialty" for c in offered):
                # "lung test" only reached Pulmonology's default type; the model may know better.
                verdict = self._consult_types()
            elif self.type_model and unexplained_words(self.ix, self.slots["service"].heard,
                                                       [c.type_id for c in offered]):
                verdict = self._verify_types(offered)
            if verdict.failed:
                # The words needed the model and its answer never came: say what we understood.
                return self._ask_type(sorted(verdict.ask or [c.type_id for c in offered]), True)
            offered = _model_candidates(verdict) or offered
            s = self.slots["service"]
            alike = umbrella(self.ix, s.heard, offered[0].type_id, s.within) if len(offered) == 1 and not s.exact else ()
            if alike:
                # "my baby's checkup": the words fit Well-Child and Newborn Visit alike, so which
                # one a model or an alias weight prefers is a prior. The caller is asked.
                self.notes.append(f"umbrella: {list(alike)} fit the words alike")
                offered = [TypeCandidate(t, 1.0, "umbrella") for t in alike]
            type_ids = [c.type_id for c in offered]
            self.scores = {c.type_id: c.score for c in offered}
            if len(type_ids) == 1:
                self.slots["service"] = replace(self.slots["service"], resolved_id=type_ids[0])

        provider_ids = self._name_candidates("provider")
        if provider_ids == []:
            return self._miss("provider")
        if provider_ids:
            provider_ids = self._described(provider_ids, type_ids)
            if self.described_ask:
                return self._ask_provider(list(self.described_ask))
        if self.geo:
            scope = self._geo_scope(type_ids, provider_ids, svc is not None)
            if isinstance(scope, Plan) and provider_ids is None and len(type_ids) == 1:
                scope, type_ids = self._kin_nearby(scope, type_ids[0])
            if isinstance(scope, Plan):
                return scope
            rows, location_ids = scope
        else:
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
                provider_ids, rows_p, self.described_sites = None, rows, ()
                if self.geo:
                    # The dropped doctor may have been what chose the city: scope again without them.
                    self.area, self.dist, self.site_choice = None, {}, False
                    scope = self._geo_scope(type_ids, None, svc is not None)
                    if isinstance(scope, Plan):
                        return scope
                    rows, location_ids = scope
                    rows_p = rows
            else:
                alts = self._alternatives(rows, location_ids)
                return self._refuse("provider_type", type_id=self._best_type(rows), who=self._who(provider_ids),
                                    alternatives=alts)

        # "Dr. Michael Sato, the one at the Sunset clinic": with no place of their own, the caller
        # goes where they described the doctor.
        at_described_site = location_ids is None and bool(self.described_sites)
        if at_described_site:
            location_ids = list(self.described_sites)
        rows_pl = rows_p if location_ids is None else [r for r in rows_p if r.location.id in location_ids]
        if location_ids is not None and not rows_pl:
            if not at_described_site and (self._newer("service", "location") or self._newer("provider", "location")
                                          or self._area_too(location_ids)):
                self.preface += f"That's not available at {self._where(location_ids)}. "
                self.notes.append("dropped location: does not fit the newer choice")
                self.slots["location"] = Slot(turn=self.req.turn)
                dropped, location_ids, rows_pl = location_ids, None, rows_p
                anchors = tuple(self.ix.gazetteer.sites[l] for l in dropped if l in self.ix.gazetteer.sites)
                if self.geo and anchors:
                    # Search around the dropped clinic rather than the whole country.
                    scope = self._ring(anchors, type_ids, provider_ids,
                                       rows_p if provider_ids is not None else None, svc is not None)
                    if isinstance(scope, Plan):
                        return scope
                    rows, location_ids = scope
                    rows_p = rows if provider_ids is None else [r for r in rows if r.provider.id in provider_ids]
                    rows_pl = [r for r in rows_p if r.location.id in location_ids]
            else:
                return self._refuse_location(rows_p, provider_ids, location_ids, type_ids, svc is not None)

        if svc is None and self.geo:
            # No visit named yet: two live types already mean "What's the visit for?", so a
            # metro's thousands of rows are not all checked to find that out.
            seen: set[str] = set()
            for r in rows_pl:
                if r.type.id not in seen and not has_violation(check(r, self.patient)):
                    seen.add(r.type.id)
                    if len(seen) > 1:
                        return self._ask_type(sorted(seen), False)

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
        if len(live_types) > 1 or (self.doubt and svc is not None):
            return self._ask_type(live_types, svc is not None)
        type_id = live[0].type.id
        self.slots["service"] = replace(self.slots["service"], resolved_id=type_id)

        if not ok:
            return self._ask_needed(pending, issues, type_id)

        if provider_ids is not None:
            provs = sorted({r.provider.id for r in live})
            if self.provider_declined:
                return self._ask_provider(provs)
            # The rules may leave one of several same-named doctors; what the caller said about
            # them (gender, other words) is still heard.
            described = len(self.named_providers) > 1 and self.provider_clues and self.provider_clues.words
            if len(provs) > 1 or described:
                verdict = self._consult_provider(provs, type_id, location_ids)
                if verdict.ask:
                    return self._ask_provider(sorted(verdict.ask))
                if verdict.act not in provs:
                    return self._ask_provider(provs)
                provs = [verdict.act]
                ok = [r for r in ok if r.provider.id == verdict.act]
                live = [r for r in live if r.provider.id == verdict.act]
                if not ok:
                    return self._ask_needed(live, issues, type_id)
            self.slots["provider"] = replace(self.slots["provider"], resolved_id=provs[0])

        if self.site_choice:
            sites = sorted({r.location.id for r in live}, key=lambda l: (self.dist.get(l, 0.0), l))
            if 2 <= len(sites) <= MAX_SITE_CHOICES:
                verdict = self._consult_site(sites, type_id)
                if verdict.ask:
                    return self._ask_location(verdict.ask)
                if verdict.act in sites:
                    ok = [r for r in ok if r.location.id == verdict.act]
                    live = [r for r in live if r.location.id == verdict.act]
                    self.slots["location"] = replace(self.slots["location"], resolved_id=verdict.act)
                    if not ok:
                        return self._ask_needed(live, issues, type_id)

        if location_ids is not None and self.area is None:
            locs = sorted({r.location.id for r in live})
            if len(locs) > 1:
                return self._ask_location(locs)
            self.slots["location"] = replace(self.slots["location"], resolved_id=locs[0])

        found = self._nearest_first(ok) if self.widened else self.av.find(ok, self.req.time_pref, MAX_OPTIONS)
        if not found:
            return self._refuse("no_availability", type_id=type_id)
        if self.dist:
            found = self._nearer_ties(found, ok)
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
            cands = [TypeCandidate(tid, 1.0, "name") for tid in types_named(self.ix, s.heard)]
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
        if slot_name == "provider" and len(s.within) == 1:
            answer = read_confirmation(s.heard)
            if answer is not None:
                return self._confirmed_provider(s, answer)
        matcher = match_providers if slot_name == "provider" else match_locations
        cands = matcher(self.ix, s.heard, s.within or None)
        if cands:
            self.slots[slot_name] = replace(s, asks=0, candidates=tuple((c.id, c.score) for c in cands))
        return [c.id for c in cands]

    def _confirmed_provider(self, s: Slot, yes: bool) -> list[str]:
        """The caller answered "Do you mean Dr. Emily Chen?" without a name. Yes pins the slot to
        that doctor, as if they had said the full name; no leaves the other doctors of that
        surname, to be asked about."""
        pid = s.within[0]
        ni = self.ix.name_index
        ids = [pid] if yes else [o for o in ni.by_last[ni.last_of[pid]] if o != pid]
        heard = self.ix.providers[pid].name if yes else f"Dr. {self.ix.providers[pid].last_name}"
        self.notes.append(f"provider {pid} {'confirmed' if yes else 'declined'}")
        self.provider_declined = not yes
        self.slots["provider"] = replace(s, heard=heard, within=tuple(ids), asks=0,
                                         candidates=tuple((p, 1.0) for p in ids))
        return ids

    def _newer(self, a: str, b: str) -> bool:
        return self.slots[a].given and self.slots[a].turn > self.slots[b].turn

    def _best_type(self, rows: list[BookableRow]) -> str | None:
        ids = {r.type.id for r in rows}
        return min(ids, key=lambda t: (-self.scores.get(t, 0.0), t)) if ids else None

    # ---- geography (catalogs with metros or coordinates only) ---------------------------------

    def _geo_scope(self, type_ids: list[str], provider_ids: list[str] | None,
                   has_service: bool) -> Plan | tuple[list[BookableRow], list[str] | None]:
        """Rows to search and the locations they must be at (None: anywhere), from the place
        phrase, the provider and the catalog's metros. Asks which city when that is open."""
        ix = self.ix
        tset = set(type_ids)
        prov_rows = None
        prov_metros: frozenset[str] = frozenset()
        if provider_ids is not None:
            prov_rows = sorted((r for p in provider_ids for r in ix.rows_by_provider[p] if r.type.id in tset),
                               key=lambda r: r.key)
            prov_metros = frozenset(r.location.metro_id for r in prov_rows) or frozenset(
                ix.locations[l].metro_id for p in provider_ids for l in ix.providers[p].location_ids)

        place = self._place()
        if place is None:
            if provider_ids is not None:
                if len(prov_metros) > 1:
                    return self._ask_metro(prov_metros)
                return _union(self._metro_rows(type_ids, prov_metros), prov_rows), None
            if ix.multi_metro:
                return self._ask_metro(frozenset())
            return [r for tid in type_ids for r in ix.rows_by_type[tid]], None
        if not place.sites and not place.anchors:
            return self._miss("location")
        if place.guess:
            return self._confirm_place(place)

        metros = place.metro_ids(ix)
        if provider_ids is not None and len(metros) > 1 and metros & prov_metros:
            metros = metros & prov_metros
            place = self._narrow(place, metros)
        if len(metros) > 1:
            states = [p.label for p in place.anchors if p.kind == "state"]
            return self._ask_metro(metros, states[0] if len(states) == 1 else None)
        if len(metros) == 1 and not place.unknown_city and not any(over_state_line(ix, p) for p in place.anchors):
            place = self._narrow(place, metros)

        if place.sites:
            site_ids = list(place.site_ids)
            self.site_choice = len(site_ids) > 1 and all(c.via != "exact" for c in place.sites)
            anchor = ix.gazetteer.sites.get(site_ids[0])
            if anchor is None:
                return _union([r for tid in type_ids for r in ix.rows_by_type[tid]], prov_rows), site_ids
            self.dist = self._distances((anchor,), FINAL_RING_MI)
            return _union(self._rows_at(type_ids, self.dist), prov_rows), site_ids
        return self._ring(place.anchors, type_ids, provider_ids, prov_rows, has_service)

    def _kin_nearby(self, refusal: Plan, type_id: str) -> tuple[Plan | tuple[list[BookableRow], list[str] | None],
                                                                  list[str]]:
        """Nothing near the caller has the visit, but the caller's words also name a related one
        that is ("a CT scan of my chest": no CT - Chest within 50 miles, a CT Scan 3 miles away).
        That one is searched instead, and the caller is told why; else the refusal stands."""
        s = self.slots["service"]
        kin = list(fitting_kin(self.ix, s.heard, type_id)) if refusal.refusal and \
            refusal.refusal.code == "none_nearby" and s.heard and not s.exact else []
        if not kin:
            return refusal, [type_id]
        state = (self.area, self.dist, self.site_choice, self.widened, self.preface, len(self.notes))
        self.area, self.dist, self.site_choice, self.widened = None, {}, False, False
        scope = self._geo_scope(kin, None, True)
        if isinstance(scope, Plan) or all(has_violation(check(r, self.patient)) for r in scope[0]):
            self.area, self.dist, self.site_choice, self.widened, self.preface, n = state
            del self.notes[n:]
            return refusal, [type_id]
        self.notes.append(f"no {type_id} nearby: searched {kin}, which the words name too")
        self.preface = T.kin_preface(self.ix, type_id) + self.preface
        self.scores = {t: 1.0 for t in kin}
        self.slots["service"] = replace(s, resolved_id=kin[0] if len(kin) == 1 else None)
        return scope, kin

    def _ring(self, anchors: tuple[Place, ...], type_ids: list[str], provider_ids: list[str] | None,
              prov_rows: list[BookableRow] | None, has_service: bool):
        """Widen around the area until some row there passes policy or needs only an answer. A
        named city's first ring is its own clinics: a neighboring city is "nothing closer"."""
        base = min(a.radius_mi for a in anchors)
        radii = [r for r in sorted({base, 2 * base, FINAL_RING_MI}) if base <= r <= FINAL_RING_MI]
        # A doctor who does not do this visit anywhere does not narrow the search: the caller hears
        # that, with the doctors near the place who do (provider_type), not "not near here".
        allowed = set(provider_ids) if provider_ids is not None and prov_rows else None
        if not radii:
            return self._none_nearby(anchors, base, type_ids, allowed, prov_rows, has_service)
        for radius in radii:
            dist = self._distances(anchors, radius, own_metro_only=radius == radii[0])
            if allowed is None:
                cand = self._rows_at(type_ids, dist)
            else:
                cand = sorted((r for r in prov_rows or () if r.location.id in dist),
                              key=lambda r: (dist[r.location.id], r.location.id, r.key))
            # Rows come nearest first, so the first valid one is the nearest.
            nearest = next((r for r in cand if not has_violation(check(r, self.patient))), None)
            # Rows that only policy rules out end the search: that refusal is the useful answer.
            if nearest or (cand and radius == radii[-1]):
                break
        else:
            return self._none_nearby(anchors, radii[-1], type_ids, allowed, prov_rows, has_service)

        local = cand if allowed is None else self._rows_at(type_ids, dist)
        order = tuple(sorted(dist, key=lambda l: (dist[l], l)))
        self.area, self.dist, self.site_choice = Area(anchors, radius, order), dist, True
        self.notes.append(f"area {'/'.join(a.key for a in anchors)} within {radius:g} mi: {len(order)} sites")
        if nearest and radius > radii[0]:
            self.widened = True
            lid = nearest.location.id
            self.preface += T.ring_preface(self.ix, lid, dist[lid], anchors[0].label,
                                           tuple(m for a in anchors for m in a.metro_ids))
        return _union(local, prov_rows), list(order)

    def _none_nearby(self, anchors: tuple[Place, ...], radius: float, type_ids: list[str],
                     allowed: set[str] | None, prov_rows: list[BookableRow] | None, has_service: bool,
                     named: dict | None = None) -> Plan:
        """Nothing in the widest ring: name the nearest valid site, as a pickable alternative.
        `named`: the clinics the caller named, when the ring was around them."""
        named = named or {}
        ix, anchor = self.ix, anchors[0]
        who = self._who(sorted(allowed)) if allowed else None
        metros = set().union(*(ix.metros_by_type[t] for t in type_ids))
        if allowed is not None:
            metros &= {ix.locations[l].metro_id for p in allowed for l in ix.providers[p].location_ids}

        def miles_to(lat: float | None, lon: float | None) -> float:
            return haversine(anchor.lat, anchor.lon, lat, lon) if None not in (anchor.lat, lat) else math.inf

        sites = [l for m in metros for l in ix.locs_by_metro[m]]
        # A state's own clinics come first ("Kansas": Overland Park, not a nearer one in Missouri).
        inside = set(ix.gazetteer.state_sites.get(anchor.key, ()))
        for lid in sorted(sites, key=lambda l: (l not in inside, miles_to(ix.locations[l].lat, ix.locations[l].lon),
                                                l)):
            at = ([r for r in prov_rows or () if r.location.id == lid] if allowed is not None
                  else [r for tid in type_ids for r in ix.rows_by_type_loc.get((tid, lid), ())])
            valid = [r for r in at if not has_violation(check(r, self.patient))]
            if not valid:
                continue
            best = min(valid, key=lambda r: (bool(check(r, self.patient)), -self.scores.get(r.type.id, 0.0), r.key))
            alt = (best.type.id, best.provider.id, lid)
            loc = ix.locations[lid]
            return self._refuse("none_nearby", type_id=best.type.id if has_service else None, who=who,
                                alternatives=(alt,) if has_service else (), location_id=lid,
                                near=anchor.label, near_kind=anchor.kind, radius_mi=radius,
                                nearest_mi=miles_to(loc.lat, loc.lon), inside=lid in inside, **named)
        return self._refuse("none_nearby", type_id=_type_understood(type_ids, has_service),
                            who=who, near=anchor.label, near_kind=anchor.kind, radius_mi=radius, **named)

    def _ask_metro(self, metros: frozenset[str], state: str | None = None) -> Plan:
        ids = sorted(metros, key=lambda m: (self.ix.metros[m].name, self.ix.metros[m].state))
        if 1 < len(ids) <= MAX_OPTIONS:
            return self._ask("metro", options=tuple(ids))
        return self._ask("metro", context=state)

    def _confirm_place(self, place: PlaceMatch) -> Plan:
        """A guessed place in one city is confirmed by name, city and state ("Did you mean Renton,
        Washington?"); guesses in several cities are asked between."""
        ix = self.ix
        metros = place.metro_ids(ix)
        if len(metros) != 1:
            return self._ask_metro(metros)
        said = ({T.place_said(l.short_name, l.city, l.state) for l in (ix.locations[c.id] for c in place.sites)}
                | {T.place_said(p.label, p.city, p.state) for p in place.anchors})
        if len(said) != 1:
            m = ix.metros[next(iter(metros))]
            said = {T.place_said(m.name, m.name, m.state)}
        self.notes.append(f"place guessed: {said}")
        return self._ask("place_confirm", options=tuple(metros), context=said.pop())

    def _place(self) -> PlaceMatch | None:
        """The location phrase as sites or area anchors; an answer to "which city?" narrows it."""
        if self.place_memo is not None:
            return self.place_memo
        s, ix = self.slots["location"], self.ix
        if not s.heard:
            return None
        at_sites = [w for w in s.within if w in ix.locations]
        if at_sites:
            m = PlaceMatch(sites=tuple(match_locations(ix, s.heard, at_sites)))
        else:
            m = resolve_place(ix, s.heard)
            options = frozenset(w for w in s.within if w in ix.metros)
            if s.region and len(options) == 1 and only_no(s.region):
                # "No" to "Did you mean Renton, Washington?": which city is asked afresh.
                self.slots["location"] = Slot(turn=self.req.turn)
                return None
            if s.region:
                answer = resolve_place(ix, s.region)
                # "Yes" to "Did you mean Renton, Washington?", the one city asked about.
                said_yes = len(options) == 1 and bool(read_confirmation(s.region))
                chosen = options if said_yes else answer.metro_ids(ix)
                if options & chosen:
                    chosen &= options
                narrowed = self._narrow(m, chosen) if chosen else PlaceMatch()
                if narrowed.sites or narrowed.anchors:
                    # The caller said which city: the guess is settled, and the state is that city.
                    m = replace(narrowed, guess=narrowed.guess and not said_yes and answer.guess,
                                unknown_city=False)
                else:
                    # Not one of the cities asked about: the caller named another place.
                    m = answer
                    self.slots["location"] = replace(s, heard=s.region, region=None, within=())
            elif options:
                narrowed = self._narrow(m, options)
                m = narrowed if narrowed.sites or narrowed.anchors else m
        self.place_memo = m
        return m

    def _place_ids(self) -> list[str] | None:
        """Locations a picked offer may be at, for _fits_change."""
        place = self._place()
        if place is None:
            return None
        if place.sites:
            return list(place.site_ids)
        return [l for m in sorted(place.metro_ids(self.ix)) for l in self.ix.locs_by_metro[m]]

    def _narrow(self, m: PlaceMatch, metros: frozenset[str]) -> PlaceMatch:
        """Keep what lies in `metros`; a state or multi-city anchor becomes those cities."""
        sites = tuple(c for c in m.sites if self.ix.locations[c.id].metro_id in metros)
        anchors: dict[str, Place] = {}
        for p in m.anchors:
            inside = set(p.metro_ids) & metros
            if not inside:
                continue
            if p.kind == "state" or len(p.metro_ids) > len(inside):
                for mid in sorted(inside):
                    mp = self._metro_place(mid)
                    anchors[mp.key] = mp
            else:
                anchors[p.key] = p
        return replace(m, sites=sites, anchors=tuple(anchors.values()))

    def _metro_place(self, mid: str) -> Place:
        m = self.ix.metros[mid]
        return Place(f"metro:{mid}", "metro", m.name, m.lat, m.lon, (mid,), RADIUS_MI["metro"], m.name, m.state)

    def _distances(self, anchors: tuple[Place, ...], radius: float, own_metro_only: bool = False) -> dict[str, float]:
        """Miles to the nearest anchor for sites within `radius`; a city's own sites always count,
        and with `own_metro_only` they are all a city anchor reaches."""
        out: dict[str, float] = {}
        for a in anchors:
            if not (own_metro_only and a.kind == "metro"):
                for lid, d in nearby(self.ix, a, radius):
                    out[lid] = min(d, out.get(lid, math.inf))
            if a.kind == "metro":
                for mid in a.metro_ids:
                    for lid in self.ix.locs_by_metro.get(mid, ()):
                        loc = self.ix.locations[lid]
                        d = haversine(a.lat, a.lon, loc.lat, loc.lon) if loc.lat is not None else 0.0
                        out[lid] = min(d, out.get(lid, math.inf))
        return out

    def _rows_at(self, type_ids: list[str], dist: dict[str, float]) -> list[BookableRow]:
        order = sorted(dist, key=lambda l: (dist[l], l))
        return [r for lid in order for tid in type_ids for r in self.ix.rows_by_type_loc.get((tid, lid), ())]

    def _metro_rows(self, type_ids: list[str], metros: frozenset[str]) -> list[BookableRow]:
        return self._rows_at(type_ids, {l: 0.0 for m in sorted(metros) for l in self.ix.locs_by_metro.get(m, ())})

    def _area_too(self, location_ids: list[str]) -> bool:
        """"Lakewood" named a clinic that can't do this, and also the suburb it is in: search there."""
        heard = self.slots["location"].heard
        return self.geo and all(names_own_area(self.ix, heard, l) for l in location_ids)

    def _consult_site(self, sites: list[str], type_id: str) -> Verdict:
        s = self.slots["location"]
        heard = ", ".join(filter(None, [s.heard, s.region]))
        clue = self._place_clue(heard)
        if not clue:
            return DECLINE
        verdict = self.site_chooser.pick_site(heard, type_id, sites)
        self._consulted("site", verdict, f"site chooser on {list(clue)}: {verdict.describe()}")
        if verdict.failed:
            return unanswered(sites)
        if verdict.ask and not (len(verdict.ask) <= MAX_OPTIONS and set(verdict.ask) <= set(sites)):
            return DECLINE
        return verdict

    def _place_clue(self, heard: str) -> tuple[str, ...]:
        """Words of the place phrase beyond filler and the names of the places it resolved to:
        "the one on Lamar in Austin" -> ("lamar",); "Austin" or a misheard "Austen" -> ()."""
        place = self._place() or PlaceMatch()
        names: set[str] = set()
        for p in place.anchors:
            names.update(tokens(p.label))
        for c in place.sites:
            loc = self.ix.locations[c.id]
            names.update(tokens(f"{loc.name} {loc.address}"))
        for mid in place.metro_ids(self.ix):
            m = self.ix.metros[mid]
            names.update(tokens(" ".join([m.name, m.state, T.state_name(m.state), *m.aliases])))
        names -= _PLACE_FILLER
        names |= STREET_TYPES
        # A street said with its type ("Peachtree Street") that put the caller at no clinic is on
        # none of our addresses: only geography the catalog does not hold could place it.
        names |= hear_place(heard).street_words

        def is_name(w: str) -> bool:
            keys = phonetic_keys(w)
            return any(w == n or jellyfish.jaro_winkler_similarity(w, n) >= 0.9
                       or (len(w) > 3 and bool(keys & phonetic_keys(n))) for n in names)
        return tuple(w for w in tokens(heard) if w not in _PLACE_FILLER and not w.isdigit() and not is_name(w))

    def _nearest_first(self, ok: list[BookableRow]) -> list[TimeSlot]:
        """After the search widened ("the nearest is 13 miles away, in San Francisco"): openings at
        the nearest clinic first, and a farther one only for offers the nearer ones cannot fill.
        Soonest-first across a 50-mile ring offered clinics 40 miles away while one at 13 had times."""
        by_loc: dict[str, list[BookableRow]] = {}
        for r in ok:
            by_loc.setdefault(r.location.id, []).append(r)
        found: list[TimeSlot] = []
        for lid in sorted(by_loc, key=lambda l: (self.dist.get(l, math.inf), l)):
            found += self.av.find(by_loc[lid], self.req.time_pref, MAX_OPTIONS - len(found))
            if len(found) == MAX_OPTIONS:
                break
        return found

    def _nearer_ties(self, found: list[TimeSlot], ok: list[BookableRow]) -> list[TimeSlot]:
        """Soonest first; at the same start, the nearer site. Each offer may be swapped for an
        open slot at the same time at a nearer site, if the offers stay distinguishable."""
        dist = self.dist
        by_loc: dict[str, list[BookableRow]] = {}
        for r in ok:
            by_loc.setdefault(r.location.id, []).append(r)
        nearer_first = sorted(by_loc, key=lambda l: (dist.get(l, math.inf), l))
        out = list(found)
        for i, s in enumerate(out):
            mine = dist.get(s.location_id, math.inf)
            minute = s.start.hour * 60 + s.start.minute
            others = [o for j, o in enumerate(out) if j != i]
            for lid in nearer_first:
                if dist.get(lid, math.inf) >= mine:
                    break
                loc = self.ix.locations[lid]
                if s.start.weekday() not in loc.open_weekdays:
                    continue
                swapped = None
                for r in by_loc[lid]:
                    dur = r.type.duration_min
                    if minute < loc.open_minute or minute + dur > loc.close_minute or (minute - loc.open_minute) % dur:
                        continue
                    if any(o.start.date() == s.start.date() and (o.provider_id == r.provider.id or o.location_id == lid)
                           for o in others):
                        continue
                    slot = TimeSlot(r.type.id, r.provider.id, lid, s.start, dur)
                    if self.av.is_open(slot):
                        swapped = slot
                        break
                if swapped:
                    self.notes.append(f"offer {i + 1}: same time at nearer {lid}")
                    out[i] = swapped
                    break
        return out

    def _type_pool(self, pool: list[str], keep: list[str] = (), phrase: str | None = None) -> list[str]:
        """The types a model chooses among: all offered, cut to a shortlist for `phrase` (the
        service phrase) on large catalogs."""
        if len(pool) <= SHORTLIST_ABOVE:
            return pool
        s = self.slots["service"]
        place = self._place() if self.geo else None
        metros = place.metro_ids(self.ix) if place is not None else frozenset()
        return sorted(set(type_shortlist(self.ix, phrase or s.heard, s.hint, metros or None)) | set(keep))

    # ---- type choice ---------------------------------------------------------------------

    def _doubted(self, lexical: list[TypeCandidate]) -> list[TypeCandidate] | None:
        """The caller said they do not know which visit it is ("I don't remember if it goes down my
        throat or up from below"): no answer of ours commits and no model narrows it, the caller
        is asked. Each alternative they named is a visit by name or alias, else by a model's choice
        over that alternative with what they said before it. The caller is asked between the
        visits found, never to confirm one: with fewer than two found, between those and what the
        whole phrase reaches lexically, else openly ([]). Alternatives that all name one visit by
        name or alias leave no doubt about it ("a sonogram or an ultrasound, I'm not sure which
        they call it"); two descriptions a model maps to one visit do not settle it."""
        s = self.slots["service"]
        doubt = stated_doubt(self.ix, s.heard) if s.heard and not s.within else None
        if doubt is None:
            return None
        by_name = [self._named_alternative(option) for option in doubt.options]
        if None not in by_name and len(set(by_name)) == 1:
            return None
        found = self._modeled_alternatives(doubt, by_name)
        self.doubt = self.type_consulted = True
        self.notes.append(f"caller unsure between {list(doubt.options)}: {found}")
        named = list(dict.fromkeys(t for t in found if t))
        if len(named) < 2:
            top = lexical[0].score if lexical else 0.0
            named = list(dict.fromkeys(named + [c.type_id for c in lexical if c.score >= top - TYPE_TIE_GAP
                                                and c.type_id not in self.ix.unoffered_types]))
        if len(named) == 1 and named[0] in by_name:
            # "the cleaning or the exam": the caller named one visit, and the other alternative
            # found none or the same; it is most likely that visit's nearest neighbour (Dental Exam).
            offered = [t for t in self.ix.types if t not in self.ix.unoffered_types]
            other = " ".join(o for t, o in zip(by_name, doubt.options) if t is None)
            neighbour = nearest_type(self.ix, named[0], offered, other, s.hint)
            named += [neighbour] if neighbour else []
        if not 2 <= len(named) <= MAX_OPTIONS:
            return []
        self.slots["service"] = replace(s, asks=0, candidates=tuple((t, 1.0) for t in named))
        return [TypeCandidate(t, 1.0, "doubt") for t in named]

    def _named_alternative(self, option: str) -> str | None:
        named = [c for c in match_types(self.ix, option, self.slots["service"].hint)
                 if c.via != "specialty" and c.type_id not in self.ix.unoffered_types]
        tier = {c.type_id for c in named if c.score >= named[0].score - TYPE_TIE_GAP} if named else set()
        return tier.pop() if len(tier) == 1 else None

    def _modeled_alternatives(self, doubt: Doubt, found: list[str | None]) -> list[str | None]:
        """A model's choice for each alternative no name or alias found. The questions do not
        depend on each other, so they are all sent before the first answer is awaited."""
        if not self.type_model:
            return found
        hint = self.slots["service"].hint
        offered = sorted(t for t in self.ix.types if t not in self.ix.unoffered_types)
        questions: dict[str, tuple[str, list[str]]] = {}
        for t, option in zip(found, doubt.options):
            if t is None and option not in questions:
                phrase = f"{doubt.sure}, {option}" if doubt.sure else option
                questions[option] = (phrase, self._type_pool(offered, phrase=phrase))
        for phrase, pool in questions.values():
            self.dis.prefetch_pick(phrase, hint, pool)
        chosen: dict[str, str | None] = {}
        for option, (phrase, pool) in questions.items():
            verdict = self.dis.pick_type(phrase, hint, pool)
            self._consulted("type", verdict, f"type disambiguator on {option!r}: {verdict.describe()}")
            chosen[option] = verdict.act if verdict.act in pool else None
        return [t or chosen.get(option) for t, option in zip(found, doubt.options)]

    def _consult_types(self) -> Verdict:
        """Ask the type disambiguator over every offered type (or the options the caller is
        answering). Unoffered types are never candidates: "we don't offer that" stays lexical."""
        s = self.slots["service"]
        if not self.type_model or not s.heard or self.type_consulted:
            return DECLINE
        self.type_consulted = True
        full = [t for t in (s.within or sorted(self.ix.types)) if t not in self.ix.unoffered_types]
        pool = self._type_pool(full)
        return self._model_types(pool, (), shortlisted=len(pool) < len(full))

    def _offered_instead(self, unoffered: str) -> Verdict:
        """Only a body word pointed at a visit no clinic offers ("my eyes get itchy and watery" ->
        eye care): the caller did not ask for that visit, so the model hears the whole phrase over
        every offered visit and that one. Acts only on an offered visit the check confirms against
        it; anything else declines, and the caller hears that we do not offer it."""
        s = self.slots["service"]
        if not self.type_model or self.type_consulted:
            return DECLINE
        self.type_consulted = True
        full = [t for t in (s.within or sorted(self.ix.types)) if t not in self.ix.unoffered_types]
        pool = sorted(set(self._type_pool(full, [unoffered])) | {unoffered})
        first = self.dis.pick_type(s.heard, s.hint, pool)
        self._consulted("type", first, f"type disambiguator beside unoffered {unoffered}: {first.describe()}")
        if first.failed or first.act not in set(pool) - {unoffered}:
            return DECLINE
        checked = self.dis.check_type(s.heard, s.hint, first, unoffered)
        if checked is None:
            return DECLINE
        self._consulted("type check", checked, f"type check {first.act} vs {unoffered}: {checked.describe()}")
        return checked if checked.act == first.act else DECLINE

    def _verify_types(self, lexical: list[TypeCandidate]) -> Verdict:
        """The lexicon matched, but the caller said more than the matched names and aliases explain
        ("shots before my trip to Thailand", "my hay fever", "checkups while I'm expecting"): the
        model hears the whole phrase over every offered type, and may confirm the match, replace
        it, or leave two to ask between. A bare "MRI" or "checkup" never gets here: the model's
        answer would be a prior ("MRI" -> brain 0.89), and the caller is asked instead."""
        if self.type_consulted:
            return DECLINE
        self.type_consulted = True
        ids = [c.type_id for c in lexical]
        # An answer to "Is that A or B?" that matched one of them is heard among A and B only.
        asked = self.slots["service"].within if set(ids) <= set(self.slots["service"].within) else ()
        full = sorted(t for t in (asked or self.ix.types) if t not in self.ix.unoffered_types)
        pool = self._type_pool(full, ids)
        return self._model_types(pool, ids, shortlisted=len(pool) < len(full))

    def _model_types(self, pool: list[str], lexical: list[str], shortlisted: bool = False) -> Verdict:
        """A choice over `pool` (`shortlisted`: cut from every offered type), then a check of its
        front-runner against the runner-up, or against the lexical match when the choice went
        elsewhere. Declined: the model had nothing to add. Failed: no usable answer came, and the
        caller is asked among its `ask` (none: among what the lexicon understood)."""
        s = self.slots["service"]
        if len(lexical) == 2:
            # A tie the choice breaks is mostly checked between the two: both requests at once.
            self.dis.prefetch_check(s.heard, s.hint, (lexical[0], lexical[1]))
        first = self.dis.pick_type(s.heard, s.hint, pool)
        self._consulted("type", first, f"type disambiguator: {first.describe()}")
        if first.failed:
            return first
        ids = [first.act] if first.act else list(first.ask or ())
        if not ids or not set(ids) <= set(pool) or lexical == [first.act]:
            return DECLINE
        if shortlisted:
            first, pool = self._within_specialty(first, pool)
            if first.failed:
                return first
            ids = [first.act] if first.act else list(first.ask or ())
        chosen = ids[0]
        rival = self._rival(first, chosen, pool, lexical)
        if rival is not None:
            checked = self.dis.check_type(s.heard, s.hint, first, rival)
            if checked is None and lexical and (len(lexical) == 1 or not first.act):
                # A model with no second question may break a lexical tie, but does not overrule
                # a lexical match on its own word.
                return DECLINE
            first = checked or first
            self._consulted("type check", first, f"type check {chosen} vs {rival}: {first.describe()}")
            if first.ask and not first.failed and lexical and set(first.ask) <= set(lexical):
                # The check did not confirm a choice among visits the words fit alike: which one
                # the model preferred is a prior ("my doctor ordered an MRI" -> brain), so the
                # caller is asked among all of them, as without a model.
                return DECLINE
        ids = [first.act] if first.act else list(first.ask or ())
        if not set(ids) <= set(pool):
            # The check gate only settles on the two it weighed; anything else is no usable answer.
            self.notes.append("type check answered outside the options: asking")
            first = unanswered(t for t in (chosen, rival) if t in pool)
            ids = list(first.ask)
        self.slots["service"] = replace(s, asks=0, candidates=tuple((t, 1.0) for t in ids))
        return first

    def _within_specialty(self, first: Verdict, pool: list[str]) -> tuple[Verdict, list[str]]:
        """A shortlist offers a specialty no word of the phrase reached by its default visit only.
        A choice of that default named the specialty, not the visit ("hot flashes, night sweats"
        -> OB/GYN New Patient Visit): the model chooses again among that specialty's visits."""
        chosen = first.act
        spec = self.ix.types[chosen].specialty if chosen else None
        if spec is None or self.ix.specialty_default.get(spec) != chosen:
            return first, pool
        place = self._place() if self.geo else None
        metros = place.metro_ids(self.ix) if place is not None else frozenset()
        siblings = sorted(t for t, ty in self.ix.types.items() if ty.specialty == spec
                          and t not in self.ix.unoffered_types and (not metros or self.ix.metros_by_type[t] & metros))
        if set(siblings) <= set(pool):
            return first, pool
        s = self.slots["service"]
        second = self.dis.pick_type(s.heard, s.hint, siblings)
        self._consulted("type", second, f"type disambiguator within {spec}: {second.describe()}")
        if second.failed:
            # The caller is asked what the model was: the specialty's visits, openly past three.
            return unanswered(siblings), pool
        if not (second.act or second.ask):
            return first, pool
        return second, sorted(set(pool) | set(siblings))

    def _rival(self, first: Verdict, chosen: str, pool: list[str], lexical: list[str]) -> str | None:
        """What the check weighs the choice against: the other option of a pair; the lexical match
        the choice overruled; the runner-up if the first answer gave it at least RIVAL_MIN_P; else
        the chosen type's nearest neighbour in the pool (lexicon.nearest_type). A long-shot
        runner-up is checked only when no neighbour exists, so the check is never skipped."""
        if first.ask:
            return first.ask[1]
        if lexical and chosen not in lexical:
            return lexical[0]
        runner = next(((t, p) for t, p in first.top if t in pool and t != chosen), None)
        if runner and runner[1] >= RIVAL_MIN_P:
            return runner[0]
        s = self.slots["service"]
        return nearest_type(self.ix, chosen, pool, s.heard, s.hint) or (runner[0] if runner else None)

    def _described(self, named: list[str], type_ids: list[str]) -> list[str]:
        """The doctors the caller means: those the name matches, narrowed by the catalog facts
        they gave (language, title, specialty, site) before any booking rule. "The nurse
        practitioner, Dr. Hernandez" is the nurse practitioner even when she takes no new patients,
        so the refusal says so instead of offering another Dr. Hernandez. Gender and other words
        are weighed later, among the doctors the rules leave (_consult_provider)."""
        self.named_providers = named
        clues = self.provider_clues = read_provider_clues(self.ix, self.slots["provider"].heard, named)
        if clues.negated or not clues.facts:
            return named
        self.notes.append(f"provider facts {list(clues.facts)}: {list(clues.fits)}")
        if not self.slots["location"].heard:
            self.described_sites = clues.sites
        if clues.gender and len(clues.fits) == 1 < len(named):
            # "Dr. Singh, he speaks Vietnamese": the language singles out Dr. Olivia Singh, but the
            # gender said is part of the description too, so no rule speaks for her before it is
            # settled, as in _consult_provider: with no sure answer she is confirmed by full name;
            # a sure answer of the other gender asks among every Dr. Singh who can take the visit.
            asked = self.chooser.provider_genders(list(clues.fits))
            gender = gender_of((asked or {}).get(clues.fits[0]))
            self._consulted("provider gender", DECLINE if asked is None else Verdict(called=True, failed=not asked),
                            f"provider gender {clues.gender} of the one the facts leave: {gender}")
            if gender is None:
                self.described_ask = clues.fits
            elif gender != clues.gender:
                tset = set(type_ids)
                self.described_ask = tuple(sorted({r.provider.id for p in named for r in self.ix.rows_by_provider[p]
                                                   if r.type.id in tset and not has_violation(check(r, self.patient))})
                                           or named)
        return list(clues.fits)

    def _bookable(self, provider_ids: list[str], type_id: str, location_ids: list[str] | None) -> list[str]:
        """Of these doctors, those with this visit (at these clinics) that passes policy or needs
        only an answer."""
        return sorted({r.provider.id for p in provider_ids for r in self.ix.rows_by_provider[p]
                       if r.type.id == type_id and (location_ids is None or r.location.id in location_ids)
                       and not has_violation(check(r, self.patient))})

    def _consult_provider(self, provs: list[str], type_id: str, location_ids: list[str] | None) -> Verdict:
        """Splits same-named providers by what the caller said about them beyond the catalog
        facts (_described already applied those): gender, which only the model can read off a
        first name, then any words left, which only the model can weigh. Asks among whoever is left.

        Inferred gender narrows but never books on its own. Only a sure answer rules a doctor out
        (decision.gender_of); a doctor it alone singles out is confirmed by full name ("Do you mean
        Dr. Emily Chen?"), and so is the one doctor the facts leave when the model cannot say they
        are the gender the caller said. A gender that rules out everyone the facts left asks among
        every doctor of that name who can take the visit."""
        heard = self.slots["provider"].heard or ""
        clues = self.provider_clues
        if clues is None or not clues.words:
            return DECLINE
        if clues.negated:
            self.notes.append(f"provider clues negated {list(clues.words)}: asking")
            return Verdict(ask=tuple(provs))
        fits = list(provs)
        if clues.gender:
            asked = self.chooser.provider_genders(fits)
            p_woman = asked or {}
            known = {p: g for p in fits if (g := gender_of(p_woman.get(p)))}
            kept = [p for p in fits if known.get(p, clues.gender) == clues.gender]
            # A model call only when a question was asked; no answer to it leaves every gender unknown.
            self._consulted("provider gender", DECLINE if asked is None else Verdict(called=True, failed=not asked),
                            f"provider gender {clues.gender}: {kept} of {fits}, p(woman) "
                            + " ".join(f"{p}={v:.2f}" for p, v in sorted(p_woman.items())))
            if not kept:
                return Verdict(ask=tuple(self._bookable(self.named_providers, type_id, location_ids) or provs))
            if len(kept) == 1 and (len(fits) > 1 or known.get(kept[0]) != clues.gender):
                return Verdict(ask=(kept[0],))
            fits = kept
        if len(fits) == 1:
            return Verdict(act=fits[0])
        if clues.rest:
            verdict = self.chooser.pick_provider(heard, type_id, fits)
            self._consulted("provider", verdict, f"provider chooser on {list(clues.rest)}: {verdict.describe()}")
            if verdict.failed:
                return unanswered(fits)
            if verdict.act in fits or (verdict.ask and set(verdict.ask) <= set(fits)):
                return verdict
        return Verdict(ask=tuple(fits))

    def _ask_needed(self, pending: list[BookableRow], issues: dict[tuple, list[Violation]], type_id: str) -> Plan:
        fields = Counter(v.field for r in pending for v in issues[r.key] if v.kind is IssueKind.NEEDS_INFO)
        field = max(("is_new", "has_referral"), key=lambda f: fields.get(f, 0))
        return self._ask(field, context=type_id)

    def _ask_type(self, live_types: list[str], has_service: bool) -> Plan:
        if not has_service or len(live_types) > MAX_OPTIONS:
            return self._ask("service_open")
        return self._ask("service", options=tuple(live_types))

    def _ask_location(self, location_ids) -> Plan:
        if len(location_ids) <= MAX_OPTIONS:
            return self._ask("location", options=tuple(sorted(location_ids)))
        return self._ask("location_open")

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
        patient now (needs-info rows count, the caller can still answer). The named clinics first,
        then by distance; a doctor already suggested is passed over only for an equally close one
        ("Dr. Maria Garcia at Mission Bay" twice would be unanswerable), never for a farther one."""
        near = self.dist
        valid = sorted((r for r in rows if r.provider.id not in exclude_providers
                        and not has_violation(check(r, self.patient))),
                       key=lambda r: (bool(location_ids) and r.location.id not in location_ids,
                                      near.get(r.location.id, 0.0), bool(check(r, self.patient)), r.key))
        out: list[BookableRow] = []
        for r in valid:
            if any(o.provider.name == r.provider.name
                   and near.get(o.location.id, 0.0) == near.get(r.location.id, 0.0) for o in out):
                continue
            out.append(r)
            if len(out) == 2:
                break
        return tuple(r.key for r in out)

    def _refuse_location(self, rows_p, provider_ids, location_ids, type_ids, has_service) -> Plan:
        type_id = self._best_type(rows_p) or _type_understood(type_ids, has_service)
        alts = self._alternatives(rows_p, None)
        named = {"at": tuple(location_ids), "on_street": self._named_by_street()}
        # The doctors meant who do this visit: "Dr. Chen at Mission Bay" for a follow-up is not at
        # Downtown, though a cardiologist Dr. Chen is.
        doers = sorted({r.provider.id for r in rows_p}) or provider_ids
        if provider_ids is not None and not any(set(self.ix.providers[p].location_ids) & set(location_ids)
                                                for p in doers):
            single = doers[0] if len(doers) == 1 else None
            return self._refuse("provider_location", type_id=type_id, who=self._who(doers),
                                provider_id=single, alternatives=alts, **named)
        anchors = tuple(self.ix.gazetteer.sites[l] for l in location_ids if l in self.ix.gazetteer.sites)
        if not rows_p and provider_ids is None and anchors:
            # Not even the widest ring around the clinic has the visit: name the nearest that does.
            return self._none_nearby(anchors, FINAL_RING_MI, type_ids, None, None, has_service, named)
        return self._refuse("location_type", type_id=type_id, alternatives=alts, **named)

    def _refuse_not_offered(self, type_id: str) -> Plan:
        """"PT for my sore knee": no physical therapy here, but the visit the knee points to is
        suggested, if this patient can book it."""
        s = self.slots["service"]
        alt = pointed_default(self.ix, s.heard, s.hint)
        if alt in self.ix.unoffered_types or not any(not has_violation(check(r, self.patient))
                                                     for r in self.ix.rows_by_type.get(alt, ())):
            alt = None
        return self._refuse("not_offered", type_id=type_id, specialty=self.ix.types[type_id].specialty,
                            alt_type_id=alt)

    def _named_by_street(self) -> bool:
        sites = self.place_memo.sites if self.place_memo else ()
        return bool(sites) and all(c.by_street for c in sites)

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

    def _consulted(self, purpose: str, verdict: Verdict, note: str) -> None:
        self.consults.append((purpose, verdict))
        self.notes.append(note)

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
                    summary=summary, notes=tuple(self.notes), consults=tuple(self.consults),
                    valid_rows=self.valid_rows, result=result,
                    area=self.area)


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


def _type_understood(type_ids: list[str], has_service: bool) -> str | None:
    """The visit a refusal names when no row says which: the one type the caller's words reached."""
    return type_ids[0] if has_service and len(type_ids) == 1 else None


def _model_candidates(verdict: Verdict) -> list[TypeCandidate]:
    """The types a model answer leaves: the one it acted on, or the ones it asks between."""
    ids = [verdict.act] if verdict.act else list(verdict.ask or ())
    return [TypeCandidate(t, 1.0, "model") for t in ids]


def _union(rows: list[BookableRow], more: list[BookableRow] | None) -> list[BookableRow]:
    if not more:
        return rows
    seen = {r.key for r in rows}
    return rows + [r for r in more if r.key not in seen]


# Words a place phrase carries that say nothing about which clinic: "I'm over near the clinic in".
_PLACE_FILLER = frozenset({
    "i", "m", "am", "im", "we", "re", "live", "living", "work", "stay", "staying", "located", "based", "in", "at",
    "from", "um", "uh", "so", "well", "the", "over", "out", "here", "somewhere", "anywhere", "just", "a", "an",
    "s", "it", "one", "of", "near", "nearby", "around", "by", "close", "closest", "nearest", "next", "to", "area",
    "please", "neighborhood", "region", "metro", "city", "town", "health", "center", "centre", "clinic",
    "clinics", "family", "specialty", "medical", "group", "community", "care", "office", "location", "on",
    "and", "my", "me", "that", "is", "there", "you", "your", "any", "some", "okay", "ok", "yes", "yeah", "like",
    "want", "would", "prefer", "go", "get", "see", "can", "could", "for", "with", "us", "our", "place", "zip",
    "zipcode", "code", "postal"})


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


def _option_labels(ix: CatalogIndex, field: str, options: tuple[str, ...]) -> list[str]:
    if field in ("metro", "place_confirm"):
        return T.metro_labels(ix, list(options))
    return [_option_label(ix, field, o) for o in options]


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
            "field": ask.field, "options": _option_labels(ix, ask.field, ask.options)}
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
