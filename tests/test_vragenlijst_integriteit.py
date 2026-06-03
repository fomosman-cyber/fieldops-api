"""Integriteits-/accuratesse-test voor de NEN-inspectie-vragenlijsten (B7).

Borgt dat elke vraag in de kunstwerk-inspectie-checklists een geldig type, een
norm-referentie en consistente velden heeft. Dit is de 'NEN-check': het codificeert
de aannames waar de NEN 2767-2 / CROW 134-conforme vragenlijsten aan moeten voldoen,
zodat accuratesse-regressies meteen opvallen.
"""
import re

import kunstwerken_taxonomy as kt

_VALID_TYPES = {"score_1_6", "ja_nee", "ja_nee_nvt", "keuze", "meting", "tekst"}
# attention_when (welk antwoord = aandachtspunt) is alleen logisch bij ja/nee-vragen.
_ATTENTION_TYPES = {"ja_nee", "ja_nee_nvt"}


def _all_questions():
    qs = list(kt.GENERIEKE_VRAGEN)
    for group in kt.VRAGEN_PER_GROEP.values():
        qs += group
    for elem in kt.VRAGEN_PER_ELEMENT.values():
        qs += elem
    return qs


def test_question_codes_uniek():
    codes = [q["code"] for q in _all_questions()]
    dupes = sorted({c for c in codes if codes.count(c) > 1})
    assert not dupes, f"Dubbele vraag-codes: {dupes}"


def test_elke_vraag_heeft_geldige_kernvelden():
    problemen = []
    for q in _all_questions():
        code = q.get("code") or "<geen code>"
        if not q.get("code"):
            problemen.append(f"{code}: geen code")
        if not q.get("vraag"):
            problemen.append(f"{code}: lege vraagtekst")
        if q.get("type") not in _VALID_TYPES:
            problemen.append(f"{code}: ongeldig type {q.get('type')!r}")
        if not q.get("norm_ref"):
            problemen.append(f"{code}: geen norm_ref (NEN/CROW-traceerbaarheid)")
    assert not problemen, "Vragenlijst-problemen:\n" + "\n".join(problemen)


def test_keuze_heeft_minstens_twee_opties():
    for q in _all_questions():
        if q.get("type") == "keuze":
            opties = q.get("opties") or []
            assert len(opties) >= 2, f"{q['code']}: keuze-vraag met < 2 opties"


def test_meting_heeft_eenheid():
    for q in _all_questions():
        if q.get("type") == "meting":
            assert q.get("eenheid"), f"{q['code']}: meting zonder eenheid"


def test_attention_when_is_bool_en_alleen_op_janee():
    for q in _all_questions():
        if "attention_when" in q:
            assert isinstance(q["attention_when"], bool), f"{q['code']}: attention_when niet bool"
            assert q["type"] in _ATTENTION_TYPES, (
                f"{q['code']}: attention_when op type {q['type']} "
                f"(alleen ja_nee/ja_nee_nvt is logisch)"
            )


def test_conditiescore_gebruikt_nen2767_schaal():
    # NEN 2767-2 conditie = schaal 1-6. De algemene-staat-vraag moet die schaal gebruiken.
    staat = [q for q in _all_questions() if q["code"].endswith(".STAAT")]
    assert staat, "Geen algemene-staat-vraag gevonden"
    for q in staat:
        assert q["type"] == "score_1_6", f"{q['code']}: conditie moet NEN 2767 (1-6) zijn"


def test_vragen_voor_levert_generieke_vragen_voor_elk_element():
    gen_codes = {q["code"] for q in kt.GENERIEKE_VRAGEN}
    for ktype in kt.KUNSTWERK_TYPES:
        for el in kt.elementen_voor(ktype):
            codes = {q["code"] for q in kt.vragen_voor(ktype, el["code"])}
            assert gen_codes <= codes, f"{ktype}/{el['code']}: mist generieke vragen"


def test_norm_ref_verwijst_naar_bekende_norm():
    # Volledige set legitieme NL-infra-normen/richtlijnen die in de checklists
    # voorkomen — niet alleen NEN/CROW, ook RWS/Waterwet/NTS/Bouwbesluit/BW etc.
    # voor tunnels, sluizen, riolering en bomen.
    norm_pat = re.compile(
        r"NEN|CROW|EN[\s-]?117[67]|13508|VTA|Mattheck|NTS|RWS|Waterwet|KRW|"
        r"Bouwbesluit|ARBO|NOC|Zorgplicht|\bBW\b|Fabrikant|reinigingsplan|Warenwet",
        re.I,
    )
    bad = [q["code"] for q in _all_questions()
           if not norm_pat.search(q.get("norm_ref", ""))]
    assert not bad, f"Vragen met niet-herkende norm_ref: {bad}"


def test_alle_question_codes_consistent_met_helper():
    assert kt.alle_question_codes() == {q["code"] for q in _all_questions()}
