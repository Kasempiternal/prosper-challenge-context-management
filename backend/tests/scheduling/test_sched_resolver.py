import pytest

from scheduling.request import Request, Update, merge
from scheduling.resolver import NoDisambiguator, resolve

EXISTING_REF = {"is_new": False, "has_referral": True}
NEW_REF = {"is_new": True, "has_referral": True}


def _rows(plan):
    return {(o.type_id, o.provider_id, o.location_id) for o in plan.offers}


def test_new_patient_chen_cardiology_offers_only_emily(converse):
    plan = converse({**NEW_REF, "service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen"})
    assert plan.status == "offer"
    assert {p for _, p, _ in _rows(plan)} == {"prov_046"}
    assert plan.say.startswith("For a cardiology consultation, Dr. Emily Chen has ")
    assert "prov_000" not in str(plan.offers)


def test_existing_patient_chen_cardiology_asks_which_chen(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen"})
    assert plan.status == "ask"
    assert (plan.ask.field, plan.ask.options) == ("provider", ("prov_000", "prov_046"))
    assert plan.say == "Do you mean Dr. David Chen or Dr. Emily Chen?"


def test_answer_david_is_scoped_to_asked_options(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen"},
                    {"provider_phrase": "David"})
    assert plan.status == "offer"
    assert {p for _, p, _ in _rows(plan)} == {"prov_000"}


def test_new_patient_knee_mri_refused(converse):
    plan = converse({"is_new": True, "service_phrase": "MRI of my knee", "provider_phrase": "Dr. Nwin"})
    assert plan.status == "refuse"
    assert plan.refusal.code == "new_patient_type"
    assert plan.say == ("A knee MRI is only for established patients and needs a referral, "
                        "so I can't book it for a new patient.")


def test_eye_exam_not_offered(converse):
    plan = converse({"service_phrase": "eye exam"})
    assert (plan.status, plan.refusal.code) == ("refuse", "not_offered")
    assert plan.say == "Sorry, we don't offer eye care at our clinics."


@pytest.mark.parametrize("phrase", ["PT for my sore knee", "I need PT for my hip",
                                    "my knee needs some PT"])
def test_a_named_visit_we_do_not_offer_is_refused_with_the_body_part_s_visit_offered_instead(index, availability,
                                                                                              phrase):
    """Held-out round 3: "start PT after my shoulder surgery" was offered an orthopedic consultation,
    the default of the specialty "shoulder" points to. It is refused, and that consultation is
    suggested; taking the suggestion books it."""

    class WouldPickOrthopedics(NoDisambiguator):
        def pick_type(self, phrase, hint, candidate_ids):
            raise AssertionError("an unoffered visit the caller named never reaches the model")

    req = merge(Request(), Update.from_args({**EXISTING_REF, "service_phrase": phrase}))
    for hooks in ({}, {"disambiguator": WouldPickOrthopedics()}):
        plan = resolve(index, req, availability, **hooks)
        assert (plan.status, plan.refusal.code, plan.say) == (
            "refuse", "not_offered", "Sorry, we don't offer physical therapy at our clinics. I can book an orthopedic "
                                     "consultation instead. Want that?")
        taken = resolve(index, merge(plan.req, Update.from_args({"pick_offer": 1})), availability, **hooks)
        assert taken.status == "offer" and {o.type_id for o in taken.offers} == {"appt_032"}


@pytest.mark.parametrize("phrase, options", [
    ("either a colonoscopy or an endoscopy, I'm not sure", ("appt_054", "appt_055")),
    ("I can't remember whether it's the cleaning or the dental exam", ("appt_074", "appt_075")),
    # One alternative named: never confirmed alone, but asked with its nearest neighbour (lexicon.nearest_type).
    ("I don't remember if it was a colonoscopy or the other one", ("appt_053", "appt_054")),
])
def test_a_caller_who_says_they_do_not_know_which_visit_is_asked_never_booked(converse, phrase, options):
    plan = converse({**EXISTING_REF, "service_phrase": phrase})
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", options)


@pytest.mark.parametrize("phrase", ["a flu shot, not sure what day works", "flu shot, not sure if Monday or Tuesday",
                                    "a sonogram or an ultrasound, I don't know what they call it"])
def test_a_doubt_that_names_no_two_visits_books_as_usual(converse, phrase):
    plan = converse({**EXISTING_REF, "service_phrase": phrase})
    assert plan.status == "offer"


def test_a_described_doctor_policy_excludes_is_refused_not_replaced(converse):
    """Held-out round 3: "the nurse practitioner, Dr. Hernandez" (a new patient) was offered Dr. Tomas
    Hernandez. The physician assistant Michael Sato takes no new patients; two other Dr. Satos do."""
    plan = converse({**NEW_REF, "service_phrase": "flu shot", "provider_phrase": "Sato, the PA"})
    assert (plan.status, plan.refusal.code) == ("refuse", "new_patient_provider")
    assert plan.say.startswith("Dr. Michael Sato isn't taking new patients. I can book ")
    assert "prov_026" not in {p for _, p, _ in plan.refusal.alternatives}


def test_a_described_doctor_who_does_not_offer_the_visit_is_refused_not_replaced(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "flu shot", "provider_phrase": "Dr. Chen, the cardiologist"})
    assert (plan.status, plan.refusal.code) == ("refuse", "provider_type")
    assert plan.say.startswith("I can't book a flu shot with Dr. Chen.")


def test_a_clinic_named_to_describe_the_doctor_is_where_the_caller_goes(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "annual physical", "provider_phrase": "Chen, at North Gate"})
    assert _rows(plan) and {(p, loc) for _, p, loc in _rows(plan)} == {("prov_004", "loc_003")}
    # A place of the caller's own wins: the clinic only told the doctors apart.
    plan = converse({**EXISTING_REF, "service_phrase": "annual physical", "provider_phrase": "Chen, at North Gate",
                     "location_phrase": "Mission Bay"})
    assert {(p, loc) for _, p, loc in _rows(plan)} == {("prov_004", "loc_000")}


def test_a_described_clinic_that_cannot_do_the_visit_is_said_and_asked_about(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "blood draw", "provider_phrase": "Dr. Sato, the one at North Beach"})
    assert (plan.status, plan.refusal.code) == ("refuse", "location_type")
    assert plan.refusal.alternatives == (("appt_072", "prov_038", "loc_000"),)
    assert plan.say == "We can't do a blood draw at North Beach. I can book Dr. Leila Sato at Mission Bay. Would that work?"


def test_knee_mri_at_north_beach_refused_with_imaging_alternatives(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "MRI knee", "location_phrase": "North Beach"})
    assert (plan.status, plan.refusal.code) == ("refuse", "location_type")
    assert set(plan.refusal.alternatives) == {("appt_065", "prov_014", "loc_004"), ("appt_065", "prov_015", "loc_005")}
    assert "Dr. Linda Ramirez at Downtown" in plan.say and "Dr. Hannah Nguyen at Midtown" in plan.say


def test_dr_nwin_knee_mri_existing_resolves_to_hannah_nguyen(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "knee MRI", "provider_phrase": "Dr. Nwin"})
    assert plan.status == "offer"
    assert {p for _, p, _ in _rows(plan)} == {"prov_015"}


def test_confusable_types_ask_either_or_without_disambiguator(converse):
    plan = converse({"service_phrase": "checkup"})
    assert (plan.status, plan.ask.field, plan.ask.options) == ("ask", "service", ("appt_002", "appt_003"))
    assert plan.say == "Is that an annual physical or an annual wellness visit?"


def test_disambiguator_used_only_for_confusable_ties(index, availability):
    from scheduling.decision import Verdict

    class Sure(NoDisambiguator):
        calls = []

        def pick_type(self, phrase, hint, candidate_ids):
            self.calls.append((phrase, len(candidate_ids)))
            return Verdict(act="appt_002", called=True)

        def check_type(self, phrase, hint, first, rival):
            return None

    sure = Sure()
    # A bare tie phrase carries no evidence for either type: the caller is asked, no model call.
    plan = resolve(index, merge(Request(), Update.from_args({"service_phrase": "checkup"})), availability,
                   disambiguator=sure)
    assert (plan.status, plan.ask.options) == ("ask", ("appt_002", "appt_003"))
    assert sure.calls == []
    req = merge(Request(), Update.from_args({"service_phrase": "routine checkup for my job"}))
    plan = resolve(index, req, availability, disambiguator=sure)
    assert plan.status == "offer" and {t for t, _, _ in _rows(plan)} == {"appt_002"}
    assert sure.calls == [("routine checkup for my job", 74)]
    sure.calls.clear()
    resolve(index, merge(Request(), Update.from_args({"service_phrase": "flu shot"})), availability, disambiguator=sure)
    assert sure.calls == []


def test_declining_disambiguator_still_asks(index, availability):
    from scheduling.decision import Verdict

    class Unsure(NoDisambiguator):
        def pick_type(self, phrase, hint, candidate_ids):
            return Verdict(called=True)

    req = merge(Request(), Update.from_args({"service_phrase": "routine checkup for my job"}))
    assert resolve(index, req, availability, disambiguator=Unsure()).status == "ask"


def test_referral_asked_only_when_every_option_needs_it(converse):
    plan = converse({"is_new": False, "service_phrase": "cardiology consultation"})
    assert (plan.status, plan.ask.field) == ("ask", "has_referral")
    plan = converse({"is_new": False, "service_phrase": "annual physical"})
    assert plan.status == "offer"


def test_new_patient_question_only_when_it_changes_the_set(converse):
    # Flu shots are open to new patients everywhere: no need to ask.
    assert converse({"service_phrase": "flu shot"}).status == "offer"
    # A follow-up is closed to new patients: asking decides whether anything is bookable.
    plan = converse({"service_phrase": "follow-up visit"})
    assert (plan.status, plan.ask.field) == ("ask", "is_new")


def test_unknown_new_status_offers_only_policy_safe_providers(converse):
    plan = converse({"has_referral": True, "service_phrase": "cardiology consultation"})
    assert plan.status == "offer"
    assert "prov_000" not in {p for _, p, _ in _rows(plan)}


def test_confusable_locations(converse):
    plan = converse({"service_phrase": "flu shot", "location_phrase": "Mission"})
    assert (plan.ask.field, plan.ask.options) == ("location", ("loc_000", "loc_001"))
    plan = converse({"service_phrase": "flu shot", "location_phrase": "Mission Bay"})
    assert {l for _, _, l in _rows(plan)} == {"loc_000"}


def test_pick_offer_confirms_and_rechecks(converse):
    plan = converse({**NEW_REF, "service_phrase": "cardiology consultation", "provider_phrase": "Dr. Emily Chen"},
                    {"pick_offer": 2})
    assert plan.status == "confirm"
    assert plan.confirm.n == 2 and plan.confirm.provider_id == "prov_046"
    assert plan.say.startswith("Okay, a cardiology consultation with Dr. Emily Chen, ")


def test_mind_change_drops_provider_that_no_longer_fits(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "annual physical", "provider_phrase": "Dr. Olivia Patel"},
                    {"service_phrase": "cardiology consultation"})
    assert plan.status == "offer"
    assert plan.say.startswith("I can't book that with Dr. Olivia Patel. For a cardiology consultation")
    assert plan.req.provider.heard is None


def test_same_turn_conflict_refuses_with_alternatives(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "annual physical", "provider_phrase": "Dr. Sofia Patel"})
    assert (plan.status, plan.refusal.code) == ("refuse", "provider_type")
    assert len(plan.refusal.alternatives) == 2


def test_unmatched_name_retries_then_hands_off(converse):
    steps = [{"provider_phrase": "Dr. Zzyzx"}, {"provider_phrase": "Dr. Qwrtp"}, {"provider_phrase": "Dr. Xxv"}]
    assert converse(*steps[:1]).ask.field == "provider_retry"
    assert converse(*steps[:2]).ask.field == "provider_spelling"
    assert converse(*steps).refusal.code == "handoff"


def test_plan_is_json_ready_and_small(converse):
    import json
    plan = converse({**NEW_REF, "service_phrase": "cardiology consultation", "provider_phrase": "Dr. Chen"})
    json.dumps(plan.tool_result())
    json.dumps(plan.req.to_dict())
    assert Request.from_dict(json.loads(json.dumps(plan.req.to_dict()))) == plan.req


def test_refusal_alternatives_are_listed_and_pickable(converse):
    first = {**EXISTING_REF, "service_phrase": "MRI knee", "location_phrase": "North Beach"}
    plan = converse(first)
    assert plan.say.endswith("Would either of those work?")
    assert plan.tool_result(speak_direct=True)["alternatives"] == [
        "1 MRI - Knee Dr. Linda Ramirez Downtown", "2 MRI - Knee Dr. Hannah Nguyen Midtown"]
    picked = converse(first, {"pick_offer": 2})
    assert picked.status == "offer"
    assert _rows(picked) == {("appt_065", "prov_015", "loc_005")}
    assert converse(first, {"pick_offer": 2}, {"pick_offer": 1}).status == "confirm"


def test_pick_with_a_change_it_satisfies_is_confirmed(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "flu shot"})
    second = plan.offers[1]
    from scheduling.request import WEEKDAY_NAMES
    day = WEEKDAY_NAMES[second.start.weekday()]
    plan = converse({**EXISTING_REF, "service_phrase": "flu shot"}, {"pick_offer": 2, "time_pref": {"days": [day]}})
    assert plan.status == "confirm"
    assert plan.confirm.start == second.start


def test_pick_with_a_change_it_breaks_is_acknowledged_not_dropped(converse):
    plan = converse({**EXISTING_REF, "service_phrase": "flu shot"},
                    {"pick_offer": 2, "location_phrase": "Sunset"})
    assert plan.status == "offer"
    assert plan.say.startswith("That time doesn't match what you just asked for, so I looked again. ")
    assert {loc for _, _, loc in _rows(plan)} == {"loc_006"}


def test_offers_are_grouped_by_doctor(converse, index):
    plan = converse({**NEW_REF, "service_phrase": "dermatology consultation"})
    names = [index.providers[o.provider_id].name for o in plan.offers]
    assert names == sorted(names, key=names.index)  # each doctor's offers are adjacent
    assert plan.say.count(" has ") == len(set(names))


def test_noon_is_spoken_as_noon():
    from datetime import datetime
    from scheduling.templates import spoken_when
    assert spoken_when(datetime(2026, 10, 9, 12, 0), datetime(2026, 10, 7, 9)) == "Friday at noon"
    assert spoken_when(datetime(2026, 10, 9, 12, 30), datetime(2026, 10, 7, 9)) == "Friday at 12:30"


def test_lookup_new_patients_follows_policy_not_the_type_flag(index):
    from scheduling.lookup import lookup
    # OB/GYN New Patient Visit allows new patients, but its only doctor is not taking any.
    [fact] = lookup(index, "do_you_offer", "OB/GYN New Patient Visit")
    assert "established patients only" in fact
    [fact] = lookup(index, "do_you_offer", "Annual Physical")
    assert "new patients welcome" in fact


class _NoSiteChooser:
    def pick_site(self, phrase, type_id, candidate_ids):
        raise AssertionError(f"site chooser consulted on SF for {phrase!r}")


@pytest.mark.parametrize("phrase", ["the one on Geary", "near Mission Bay", "the big clinic downtown", "Narnia",
                                    "I'm in San Francisco", "94103", "Mission"])
def test_sf_has_no_areas_so_the_site_chooser_never_runs(index, availability, phrase):
    req = merge(Request(), Update.from_args({**EXISTING_REF, "service_phrase": "annual physical",
                                             "location_phrase": phrase}))
    plan = resolve(index, req, availability, site_chooser=_NoSiteChooser())
    assert plan.area is None and not any(n.startswith("site chooser") for n in plan.notes)


def _with_allergy_shots():
    """SF plus a returning-only Allergy Shots type, offered by the allergists, named by its alias."""
    import json
    from pathlib import Path

    from scheduling.catalog_index import build_index

    data = Path(__file__).resolve().parents[2] / "data"
    raw = json.loads((data / "catalog.json").read_text(encoding="utf-8"))
    aliases = json.loads((data / "aliases.json").read_text(encoding="utf-8"))
    raw["appointment_types"].append({"id": "appt_900", "name": "Allergy Shots (Immunotherapy)",
                                     "specialty": "Allergy/Immunology", "duration_min": 20,
                                     "requires_referral": False, "new_patients_allowed": False})
    for p in raw["providers"]:
        if "appt_080" in p["appointment_type_ids"]:
            p["appointment_type_ids"].append("appt_900")
    aliases["aliases"]["allergy shots"] = {"appt_900": 1.0}
    return build_index(raw, aliases)


def test_a_said_alias_drops_types_whose_only_evidence_lies_inside_it():
    from scheduling.lexicon import match_types

    ix = _with_allergy_shots()
    assert [c.type_id for c in match_types(ix, "I need my allergy shots")] == ["appt_900"]
    assert "appt_080" in [c.type_id for c in match_types(ix, "my allergy consultation and my shots")]


def test_a_new_patient_asking_for_a_returning_only_type_is_refused_not_offered_a_sibling():
    from scheduling.availability import MockAvailability

    ix = _with_allergy_shots()
    plan = resolve(ix, merge(Request(), Update.from_args({**NEW_REF, "service_phrase": "I need my allergy shots"})),
                   MockAvailability(ix))
    assert (plan.status, plan.refusal.code, plan.refusal.alt_type_id) == ("refuse", "new_patient_type", "appt_080")


FLU_SHOT = {"service_phrase": "flu shot", "is_new": False}


def _plans(index, availability, *updates):
    req, plans = Request(), []
    for u in updates:
        plans.append(resolve(index, merge(req, Update.from_args(u)), availability))
        req = plans[-1].req
    return plans


def test_another_clinic_offers_the_next_clinics(index, availability):
    plans = _plans(index, availability, FLU_SHOT, {"reject": ["location"]}, {"reject": ["location"]})
    sites = [{o.location_id for o in p.offers} for p in plans]
    assert all(p.status == "offer" for p in plans)
    assert sites[0].isdisjoint(sites[1]) and (sites[0] | sites[1]).isdisjoint(sites[2])


def test_another_clinic_after_naming_one_searches_the_others(index, availability):
    named, other = _plans(index, availability, {**FLU_SHOT, "location_phrase": "North Beach"}, {"reject": ["location"]})
    assert {o.location_id for o in named.offers} == {"loc_002"}
    assert other.status == "offer" and "loc_002" not in {o.location_id for o in other.offers}
    assert "only clinic" not in other.say and other.req.location.heard is None


def test_every_clinic_turned_down_offers_them_again_and_says_so(index, availability):
    """Flu shots are at all eight SF clinics: offered three, three, one and one at a time."""
    plans = _plans(index, availability, FLU_SHOT, *[{"reject": ["location"]}] * 4)
    assert len(set().union(*({o.location_id for o in p.offers} for p in plans[:4]))) == 8
    assert plans[4].say.startswith("Those are all the clinics I have for that. For a flu shot")
    assert plans[4].offers == plans[0].offers and plans[4].req.rejected.locations == ()


def test_another_doctor_offers_other_doctors(index, availability):
    plans = _plans(index, availability, FLU_SHOT, {"reject": ["provider"]})
    before, after = ({o.provider_id for o in p.offers} for p in plans)
    assert plans[1].status == "offer" and before.isdisjoint(after)


def test_someone_else_after_naming_a_doctor_drops_the_name(index, availability):
    named, other = _plans(index, availability, {**FLU_SHOT, "provider_phrase": "Dr. Carlos Garcia"},
                          {"reject": ["provider"]})
    assert {o.provider_id for o in named.offers} == {"prov_008"}
    assert other.status == "offer" and "prov_008" not in {o.provider_id for o in other.offers}


def test_later_times_offer_none_of_the_times_turned_down(index, availability):
    plans = _plans(index, availability, FLU_SHOT, {"reject": ["time"]})
    before, after = ({o.start for o in p.offers} for p in plans)
    assert plans[1].status == "offer" and len(after) == 3 and before.isdisjoint(after)
