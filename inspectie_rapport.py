"""Narratieve tekstgeneratie voor het kunstwerken-inspectierapport (PDF).

Zet de gestructureerde inspectie-data om in leesbare Nederlandse prozatekst per
hoofdstuk, zodat de PDF een echt rapport is i.p.v. een datadump met losse
cijfers. Pure functies zonder DB/PDF-afhankelijkheid → los te unit-testen.

Conditieschaal NEN 2767-2: 1 (uitstekend) .. 6 (zeer slecht).
"""
from __future__ import annotations

import nen2767_scoring as scoring

_CONDITIE_DUIDING = {
    1: "verkeert in uitstekende staat; er zijn geen noemenswaardige gebreken vastgesteld",
    2: "verkeert in goede staat; eventuele gebreken zijn beperkt en niet urgent",
    3: "verkeert in een redelijke staat; er zijn gebreken die op termijn aandacht vragen",
    4: "verkeert in een matige staat; meerdere gebreken vragen op afzienbare termijn onderhoud",
    5: "verkeert in een slechte staat; er is sprake van serieuze gebreken die ingrijpen vergen",
    6: "verkeert in een zeer slechte staat; er zijn ernstige gebreken die acuut ingrijpen vereisen",
}


def _join(delen) -> str:
    delen = [d for d in delen if d]
    if not delen:
        return ""
    if len(delen) == 1:
        return delen[0]
    return ", ".join(delen[:-1]) + " en " + delen[-1]


def inleiding(*, kw_label, obj_naam, inspectie_type, datum_str, inspecteur, norm_ref) -> str:
    type_zin = f"een {inspectie_type.lower()}" if inspectie_type else "een visuele inspectie"
    wie = f" door {inspecteur}" if inspecteur else ""
    wanneer = f" op {datum_str}" if datum_str and datum_str != "-" else ""
    label = f" ({kw_label})" if kw_label else ""
    return (
        f"Dit rapport beschrijft de bevindingen van {type_zin} van {obj_naam}{label}, "
        f"uitgevoerd{wanneer}{wie}. De inspectie is uitgevoerd volgens {norm_ref}. "
        f"Doel van de inspectie is het objectief vaststellen van de technische staat van het "
        f"object en het onderbouwen van het benodigde onderhoud, zodat de beheerder "
        f"weloverwogen keuzes kan maken over instandhouding, planning en budget."
    )


def objectbeschrijving(*, kw_label, obj_naam, bouwjaar, beheerder, wegnr,
                       locatie_oms, coords) -> str:
    zin = f"{obj_naam} betreft {('een ' + kw_label.lower()) if kw_label else 'een kunstwerk'}"
    extra = []
    if bouwjaar:
        extra.append(f"bouwjaar {bouwjaar}")
    if beheerder:
        extra.append(f"in beheer bij {beheerder}")
    if wegnr:
        extra.append(f"gelegen aan/op {wegnr}")
    if extra:
        zin += " (" + _join(extra) + ")"
    zin += "."
    if locatie_oms:
        zin += f" {str(locatie_oms).rstrip('.')}."
    if coords:
        zin += f" De locatie is vastgelegd op coordinaten {coords}."
    return zin


def werkwijze() -> str:
    return (
        "De beoordeling is uitgevoerd volgens NEN 2767-2 (conditiemeting van infrastructuur). "
        "Per bouwdeel zijn de aangetroffen gebreken geclassificeerd naar ernst, intensiteit en "
        "omvang; daaruit volgt per gebrek een defect-score. De conditie van een bouwdeel wordt "
        "bepaald door het zwaarste gebrek (de worst-defect-regel), en de objectconditie door het "
        "slechtst scorende bouwdeel. De conditie wordt uitgedrukt op een schaal van 1 (uitstekend) "
        "tot 6 (zeer slecht). Maatregel-categorieen volgen de CROW 134-systematiek voor kunstwerken; "
        "waar verharding is beoordeeld is aanvullend CROW 146 toegepast. De genoemde kosten-ordes "
        "zijn indicatief (GWWkosten) en vervangen geen RAW-bestek."
    )


def element_alinea(*, naam, code, score, defect_count) -> str:
    code_zin = f" ({code})" if code else ""
    naam = naam or "Bouwdeel"
    if score is not None:
        kern = (f"Het bouwdeel '{naam}'{code_zin} is beoordeeld met conditiescore {score} "
                f"({scoring.conditie_label(score)}).")
    else:
        kern = f"Het bouwdeel '{naam}'{code_zin} is beoordeeld."
    if defect_count == 0:
        kern += " Hierbij zijn geen gebreken vastgelegd."
    elif defect_count == 1:
        kern += " Hierbij is 1 gebrek vastgelegd:"
    else:
        kern += f" Hierbij zijn {defect_count} gebreken vastgelegd:"
    return kern


def defect_zin(*, gebrek_naam, ernst, intensiteit, omvang, defect_score,
               crow_klasse, locatie, omschrijving, maatregel) -> str:
    cls = []
    if ernst:
        cls.append(f"ernst {ernst}")
    if intensiteit:
        cls.append(f"intensiteit {intensiteit}")
    if omvang:
        cls.append(f"omvang {omvang}")
    zin = gebrek_naam or "Gebrek"
    if locatie:
        zin += f" ter plaatse van {locatie}"
    if cls:
        zin += f" - NEN 2767-2 {_join(cls)}"
    if defect_score:
        zin += f" (defect-score {defect_score}, {scoring.conditie_label(defect_score)})"
    if crow_klasse:
        zin += f"; CROW-klasse {crow_klasse}"
    zin += "."
    if omschrijving:
        zin += f" {str(omschrijving).rstrip('.')}."
    if maatregel:
        zin += f" Geadviseerde maatregel: {str(maatregel).rstrip('.')}."
    return zin


def conditie_analyse(*, eind, defecten_totaal, defecten_kritiek,
                     elementen_beoordeeld, elementen_totaal, slechtste_naam) -> str:
    if eind is None:
        return ("De objectconditie kon nog niet worden vastgesteld omdat de inspectie "
                "onvolledig is. Een vervolg is nodig om tot een eindoordeel te komen.")
    zin = (f"Op basis van de worst-defect-methodiek is de objectconditie vastgesteld op "
           f"{eind} ({scoring.conditie_label(eind)}). Het object "
           f"{_CONDITIE_DUIDING.get(eind, '')}.")
    if slechtste_naam:
        zin += f" Deze conditie wordt bepaald door het bouwdeel '{slechtste_naam}'."
    zin += (f" In totaal zijn {defecten_totaal} gebreken vastgelegd over "
            f"{elementen_beoordeeld} van de {elementen_totaal} beoordeelde bouwdelen")
    if defecten_kritiek:
        zin += f", waarvan {defecten_kritiek} als kritiek (score 5-6) geclassificeerd."
    else:
        zin += ". Er zijn geen kritieke gebreken aangetroffen."
    return zin


def conclusie(*, eind, advies, samenvatting_vrij, aanbevolen_vrij, volgende_str) -> str:
    if eind is not None:
        zin = f"Samenvattend verkeert het object in conditie {eind} ({scoring.conditie_label(eind)}). "
    else:
        zin = "Samenvattend is het eindoordeel nog niet bepaald. "
    cat = advies.get("categorie", "-")
    actie = (advies.get("actie") or "").rstrip(".")
    termijn = advies.get("termijn_jaren")
    zin += f"De geadviseerde onderhoudscategorie is '{cat}': {actie}."
    if termijn:
        zin += f" Dit dient bij voorkeur binnen {termijn} jaar te worden opgepakt."
    if samenvatting_vrij:
        zin += f"\n\nBevinding van de inspecteur: {str(samenvatting_vrij).rstrip('.')}."
    if aanbevolen_vrij:
        zin += f"\n\nAanbevolen acties: {str(aanbevolen_vrij).rstrip('.')}."
    if volgende_str:
        zin += f"\n\nDe eerstvolgende inspectie wordt geadviseerd op {volgende_str}."
    return zin
