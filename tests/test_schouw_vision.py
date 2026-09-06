"""Beeldherkenning voor de schouw.

Drie regels die hier vastliggen:

1. **Een ongeblurd beeld gaat er niet doorheen.** Een straatbeeld bevat
   gezichten en kentekens en gaat naar een verwerker buiten de EU. Dat is een
   poort, geen vlag.
2. **Niets gemeten is niet hetzelfde als niets gevonden.** Zonder AI-sleutel, of
   bij een mislukte analyse, komt er een als onbruikbaar gemarkeerd antwoord
   terug -- geen lege lijst die als "schoon" leest.
3. **Onzekere waarnemingen scoren niet mee, maar verdwijnen ook niet.**
"""

import pytest

import schouw_vision as sv


BEELD = b"\xff\xd8\xff\xe0nep-jpeg"


# ---------------------------------------------------------------------------
# De privacy-poort
# ---------------------------------------------------------------------------

def test_ongeblurd_beeld_wordt_geweigerd():
    with pytest.raises(sv.NietGeblurd):
        sv.analyseer_frame(image_bytes=BEELD, privacy_gecontroleerd=False)


def test_leeg_beeld_wordt_geweigerd():
    with pytest.raises(ValueError):
        sv.analyseer_frame(image_bytes=b"", privacy_gecontroleerd=True)


# ---------------------------------------------------------------------------
# Zonder AI
# ---------------------------------------------------------------------------

def test_zonder_sleutel_onbruikbaar_niet_schoon(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    uit = sv.analyseer_frame(image_bytes=BEELD, privacy_gecontroleerd=True)
    assert uit["bruikbaar"] is False
    assert uit["reden_onbruikbaar"]
    assert uit["gebied"] == [] and uit["objecten"] == []


def test_kapot_antwoord_levert_geen_valse_schone_meting(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sv, "_roep_aan", lambda *a, **k: ("dit is geen json", "model-x"))
    uit = sv.analyseer_frame(image_bytes=BEELD, privacy_gecontroleerd=True)
    assert uit["bruikbaar"] is False
    assert "niet te lezen" in uit["reden_onbruikbaar"]


def test_een_kapot_frame_stopt_de_rit_niet(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")

    def ontploft(*a, **k):
        raise RuntimeError("time-out bij de API")

    monkeypatch.setattr(sv, "_roep_aan", ontploft)
    uit = sv.analyseer_frame(image_bytes=BEELD, privacy_gecontroleerd=True)
    assert uit["bruikbaar"] is False
    assert "mislukt" in uit["reden_onbruikbaar"]


# ---------------------------------------------------------------------------
# Opschonen van het antwoord
# ---------------------------------------------------------------------------

def _antwoord(monkeypatch, payload: str):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(sv, "_roep_aan", lambda *a, **k: (payload, "model-x"))
    return sv.analyseer_frame(image_bytes=BEELD, privacy_gecontroleerd=True)


def test_verzonnen_meetlat_wordt_weggefilterd(monkeypatch):
    """Een meetlat die crow_schouw niet kent kan nooit gescoord worden."""
    uit = _antwoord(monkeypatch, """{"bruikbaar": true, "gebied": [
        {"meetlat": "zwerfafval", "waarde": 3, "zekerheid": 0.9},
        {"meetlat": "kapotte_stoeptegels_gevoel", "waarde": 1, "zekerheid": 0.9}
    ], "objecten": []}""")
    assert [w["meetlat"] for w in uit["gebied"]] == ["zwerfafval"]


def test_verzonnen_objecttype_wordt_weggefilterd(monkeypatch):
    uit = _antwoord(monkeypatch, """{"bruikbaar": true, "gebied": [], "objecten": [
        {"type": "lichtmast", "aspect": "stabiliteit", "waarneming": "scheef", "zekerheid": 0.9},
        {"type": "ruimteschip", "aspect": "heelheid", "waarneming": "gedeukt", "zekerheid": 0.9}
    ]}""")
    assert [o["type"] for o in uit["objecten"]] == ["lichtmast"]


def test_json_met_tekst_eromheen_wordt_toch_gelezen(monkeypatch):
    uit = _antwoord(monkeypatch, 'Hier is mijn antwoord:\n'
                    '{"bruikbaar": true, "gebied": [], "objecten": []}\nTot zover.')
    assert uit["bruikbaar"] is True


def test_zekerheid_bepaalt_of_beoordeling_nodig_is(monkeypatch):
    uit = _antwoord(monkeypatch, """{"bruikbaar": true, "objecten": [], "gebied": [
        {"meetlat": "zwerfafval", "waarde": 2, "zekerheid": 0.95},
        {"meetlat": "bekladding", "waarde": 5, "zekerheid": 0.40}
    ]}""")
    per = {w["meetlat"]: w for w in uit["gebied"]}
    assert per["zwerfafval"]["beoordeling_nodig"] is False
    assert per["bekladding"]["beoordeling_nodig"] is True


def test_ontbrekende_zekerheid_telt_als_onzeker(monkeypatch):
    """Geen zekerheid opgegeven is geen zekerheid, niet volle zekerheid."""
    uit = _antwoord(monkeypatch, """{"bruikbaar": true, "objecten": [], "gebied": [
        {"meetlat": "zwerfafval", "waarde": 2}
    ]}""")
    assert uit["gebied"][0]["beoordeling_nodig"] is True


# ---------------------------------------------------------------------------
# Frames bundelen tot een vak
# ---------------------------------------------------------------------------

def test_slechtste_frame_bepaalt_de_waarneming():
    """Een straat met één zwaar vervuild stuk is niet half schoon.

    De veegwagen moet er hoe dan ook heen, dus middelen zou de opdracht
    verkeerd voorstellen.
    """
    frames = [
        {"bruikbaar": True, "gebied": [
            {"meetlat": "zwerfafval", "waarde": 1, "beoordeling_nodig": False}]},
        {"bruikbaar": True, "gebied": [
            {"meetlat": "zwerfafval", "waarde": 12, "beoordeling_nodig": False}]},
        {"bruikbaar": True, "gebied": [
            {"meetlat": "zwerfafval", "waarde": 2, "beoordeling_nodig": False}]},
    ]
    uit = sv.bundel_tot_waarnemingen(frames)
    assert uit["waarnemingen"]["zwerfafval"] == 12


def test_onzekere_waarneming_scoort_niet_maar_verdwijnt_niet():
    frames = [{"bruikbaar": True, "gebied": [
        {"meetlat": "zwerfafval", "waarde": 9, "beoordeling_nodig": True},
        {"meetlat": "bekladding", "waarde": 2, "beoordeling_nodig": False},
    ]}]
    uit = sv.bundel_tot_waarnemingen(frames)
    assert "zwerfafval" not in uit["waarnemingen"]
    assert uit["waarnemingen"]["bekladding"] == 2
    assert len(uit["te_beoordelen"]) == 1


def test_onbruikbare_frames_worden_geteld_niet_genegeerd():
    """Anders leest een rit door de regen als een schone wijk."""
    frames = [
        {"bruikbaar": False, "reden_onbruikbaar": "te donker"},
        {"bruikbaar": True, "gebied": [
            {"meetlat": "zwerfafval", "waarde": 1, "beoordeling_nodig": False}]},
    ]
    uit = sv.bundel_tot_waarnemingen(frames)
    assert uit["frames"] == 2
    assert uit["onbruikbare_frames"] == 1


def test_bundel_sluit_aan_op_de_scorelaag():
    """Wat hier uitkomt moet crow_schouw.beoordeel_vak in kunnen."""
    import crow_schouw as cs
    frames = [{"bruikbaar": True, "gebied": [
        {"meetlat": "zwerfafval", "waarde": 7, "beoordeling_nodig": False}]}]
    gebundeld = sv.bundel_tot_waarnemingen(frames)
    drempels = cs.Drempels({"zwerfafval": {"A+": 0, "A": 2, "B": 5, "C": 10}})
    r = cs.beoordeel_vak(gebundeld["waarnemingen"], drempels=drempels,
                         gebiedstype="woonwijk")
    assert r["beeldkwaliteit"] == "C"
    assert r["voldoet"] is False
