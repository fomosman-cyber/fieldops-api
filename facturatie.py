"""Verkoopfacturen voor de abonnementen.

Mollie factureert onze klanten niet -- Mollie factureert ons. Zonder dit bestand
krijgt een klant een afschrijving van zijn rekening zonder factuur, en dan kan
zijn boekhouder de BTW niet terugvorderen en wij hem niet verantwoorden. Voor
Nederlandse B2B is een factuur geen service maar een verplichting.

**Verkopergegevens komen uit de omgeving en zijn verplicht.** Zonder
bedrijfsnaam, adres, KvK- en BTW-nummer wordt er geen factuur gemaakt en gooit
`maak_factuur` een :class:`FactuurgegevensOntbreken`. Dat is bewust: een factuur
zonder die gegevens is juridisch geen factuur, en er half een produceren is
erger dan er geen produceren -- dan denk je dat het geregeld is.

**Het factuurnummer is doorlopend per kalenderjaar.** Een gat in de reeks moet
je kunnen uitleggen, dus het nummer wordt pas toegekend op het moment dat de rij
ook echt wordt weggeschreven, en de database dwingt uniciteit af. Botsen twee
gelijktijdige incasso's op hetzelfde nummer, dan probeert de aanroeper opnieuw
en krijgt de tweede het volgende nummer.

**Wat hier bewust niet in zit: btw-verlegging.** Een EU-klant buiten Nederland
met een geldig btw-nummer hoort 0% verlegd te krijgen. Dat is echt, maar het
vraagt validatie van het btw-nummer bij VIES en een aparte factuurtekst, en er
is nog geen buitenlandse klant. Alles is nu Nederlands 21%. Komt die klant er,
dan moet dit eerst gebouwd worden -- niet improviseren op de factuur.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.orm import Session

from models import Invoice, Organization

FACTUUR_VERSIE = "facturatie.v1-2026-09"

# Verplichte omgevingsvariabelen. Zonder deze bestaat er geen geldige factuur.
VERPLICHT = ("FIELDOPS_BEDRIJFSNAAM", "FIELDOPS_ADRES",
             "FIELDOPS_KVK", "FIELDOPS_BTW_NUMMER")


class FactuurgegevensOntbreken(RuntimeError):
    """De verkopergegevens zijn niet ingesteld op deze omgeving."""


def _env(naam: str) -> str:
    return (os.getenv(naam) or "").strip()


def verkoper() -> dict:
    """Onze eigen gegevens, zoals ze op de factuur komen."""
    ontbreekt = [n for n in VERPLICHT if not _env(n)]
    if ontbreekt:
        raise FactuurgegevensOntbreken(
            "Facturatie is niet ingesteld; ontbrekende variabelen: "
            + ", ".join(ontbreekt))
    return {
        "naam": _env("FIELDOPS_BEDRIJFSNAAM"),
        "adres": _env("FIELDOPS_ADRES"),
        "kvk": _env("FIELDOPS_KVK"),
        "btw": _env("FIELDOPS_BTW_NUMMER"),
        "iban": _env("FIELDOPS_IBAN") or None,
    }


def is_ingesteld() -> bool:
    return all(_env(n) for n in VERPLICHT)


def ontbrekende_instellingen() -> list[str]:
    return [n for n in VERPLICHT if not _env(n)]


def _rond(waarde: Decimal) -> Decimal:
    return waarde.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _bedrag(waarde: Decimal) -> str:
    return str(_rond(waarde))


def volgend_nummer(db: Session, jaar: int) -> tuple[int, str]:
    """Het volgende volgnummer in dit jaar.

    Leest het hoogste bestaande nummer en telt er een bij op. Bij gelijktijdige
    incasso's kunnen twee aanroepen hetzelfde nummer krijgen; de
    unique-constraint op (jaar, volgnummer) vangt dat af en de aanroeper
    probeert opnieuw. Dat is eenvoudiger en beter te begrijpen dan een
    aparte tellertabel, en incasso's komen niet in zulke aantallen binnen dat
    dit een knelpunt wordt.
    """
    hoogste = (db.query(Invoice.volgnummer)
                 .filter(Invoice.jaar == jaar)
                 .order_by(Invoice.volgnummer.desc())
                 .first())
    volgnummer = (hoogste[0] if hoogste else 0) + 1
    return volgnummer, f"{jaar}-{volgnummer:04d}"


def maak_factuur(db: Session,
                 org: Organization,
                 *,
                 seats: int,
                 tarief_excl: Decimal,
                 bedrag_excl: Decimal,
                 btw_percentage: Decimal,
                 btw_bedrag: Decimal,
                 bedrag_incl: Decimal,
                 periode_van: Optional[datetime] = None,
                 periode_tot: Optional[datetime] = None,
                 mollie_payment_id: Optional[str] = None,
                 factuurdatum: Optional[datetime] = None,
                 pogingen: int = 3) -> Invoice:
    """Een factuur aanmaken en wegschrijven.

    De bedragen worden meegegeven en niet hier opnieuw berekend: ze moeten
    exact overeenkomen met wat er is geincasseerd. Zou dit bestand zelf gaan
    rekenen, dan kan een factuur ooit een cent afwijken van de afschrijving en
    dat is precies het soort verschil waar een boekhouder op belt.

    Bestaat er al een factuur voor deze betaling, dan komt die terug. De
    webhook van Mollie kan meerdere keren langskomen en een tweede factuur voor
    dezelfde incasso is een boekhoudprobleem.
    """
    if mollie_payment_id:
        bestaand = (db.query(Invoice)
                      .filter(Invoice.mollie_payment_id == mollie_payment_id)
                      .first())
        if bestaand:
            return bestaand

    v = verkoper()          # gooit als de instellingen ontbreken
    nu = factuurdatum or datetime.now(timezone.utc)
    jaar = nu.year

    laatste_fout = None
    for _ in range(max(1, pogingen)):
        volgnummer, nummer = volgend_nummer(db, jaar)
        factuur = Invoice(
            organization_id=org.id,
            factuurnummer=nummer,
            jaar=jaar,
            volgnummer=volgnummer,
            factuurdatum=nu,
            vervaldatum=nu + timedelta(days=14),
            periode_van=periode_van,
            periode_tot=periode_tot,
            seats=seats,
            tarief_excl=_bedrag(tarief_excl),
            bedrag_excl=_bedrag(bedrag_excl),
            btw_percentage=str(btw_percentage),
            btw_bedrag=_bedrag(btw_bedrag),
            bedrag_incl=_bedrag(bedrag_incl),
            verkoper_naam=v["naam"],
            verkoper_adres=v["adres"],
            verkoper_kvk=v["kvk"],
            verkoper_btw=v["btw"],
            verkoper_iban=v["iban"],
            klant_naam=org.name,
            klant_adres=getattr(org, "billing_address", None),
            klant_kvk=getattr(org, "kvk_number", None),
            klant_btw=getattr(org, "btw_number", None),
            klant_email=getattr(org, "contact_email", None),
            mollie_payment_id=mollie_payment_id,
            status="betaald",
        )
        db.add(factuur)
        try:
            db.flush()
            return factuur
        except Exception as exc:  # noqa: BLE001 -- botsing op het volgnummer
            laatste_fout = exc
            db.rollback()
    raise RuntimeError(f"Factuurnummer kon niet worden toegekend: {laatste_fout}")


def als_dict(f: Invoice) -> dict:
    return {
        "id": f.id,
        "factuurnummer": f.factuurnummer,
        "factuurdatum": f.factuurdatum.isoformat() if f.factuurdatum else None,
        "periode_van": f.periode_van.isoformat() if f.periode_van else None,
        "periode_tot": f.periode_tot.isoformat() if f.periode_tot else None,
        "seats": f.seats,
        "tarief_excl": f.tarief_excl,
        "bedrag_excl": f.bedrag_excl,
        "btw_percentage": f.btw_percentage,
        "btw_bedrag": f.btw_bedrag,
        "bedrag_incl": f.bedrag_incl,
        "valuta": f.valuta,
        "status": f.status,
        "klant_naam": f.klant_naam,
        "mollie_payment_id": f.mollie_payment_id,
    }


def bouw_pdf(f: Invoice) -> bytes:
    """De factuur als PDF.

    Bevat wat een factuur volgens de Belastingdienst moet bevatten: naam en
    adres van beide partijen, ons btw-identificatienummer, het factuurnummer,
    de factuurdatum, wat er geleverd is, het bedrag exclusief btw, het
    btw-tarief en -bedrag, en het totaal.
    """
    from fpdf import FPDF

    def safe(v) -> str:
        if v is None:
            return ""
        s = str(v)
        for k, r in (("€", "EUR "), ("–", "-"), ("—", "-"),
                     ("’", "'"), ("‘", "'"), ("“", '"'),
                     ("”", '"'), ("…", "..."), ("·", "-")):
            s = s.replace(k, r)
        return s.encode("latin-1", "replace").decode("latin-1")

    pdf = FPDF(orientation="P", unit="mm", format="A4")
    pdf.set_auto_page_break(auto=True, margin=20)
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 20)
    pdf.set_xy(15, 18)
    pdf.cell(0, 10, "Factuur")

    # Verkoper rechtsboven.
    pdf.set_xy(120, 18)
    pdf.set_font("Helvetica", "B", 10)
    pdf.multi_cell(75, 5, safe(f.verkoper_naam), align="R")
    pdf.set_x(120)
    pdf.set_font("Helvetica", "", 9)
    regels = [f.verkoper_adres or ""]
    if f.verkoper_kvk:
        regels.append(f"KvK {f.verkoper_kvk}")
    if f.verkoper_btw:
        regels.append(f"BTW {f.verkoper_btw}")
    if f.verkoper_iban:
        regels.append(f"IBAN {f.verkoper_iban}")
    pdf.multi_cell(75, 4.6, safe("\n".join(x for x in regels if x)), align="R")

    # Klant links.
    pdf.set_xy(15, 42)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(0, 5, "Factuuradres", new_x="LMARGIN", new_y="NEXT")
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 10)
    klantregels = [f.klant_naam]
    if f.klant_adres:
        klantregels.append(f.klant_adres)
    if f.klant_kvk:
        klantregels.append(f"KvK {f.klant_kvk}")
    if f.klant_btw:
        klantregels.append(f"BTW {f.klant_btw}")
    pdf.multi_cell(90, 5, safe("\n".join(x for x in klantregels if x)))

    # Factuurgegevens.
    pdf.set_xy(120, 42)
    for label, waarde in (
            ("Factuurnummer", f.factuurnummer),
            ("Factuurdatum", f.factuurdatum.strftime("%d-%m-%Y") if f.factuurdatum else ""),
            ("Status", "voldaan via automatische incasso"
                       if f.status == "betaald" else f.status)):
        pdf.set_x(120)
        pdf.set_font("Helvetica", "B", 9)
        pdf.cell(30, 5, safe(label))
        pdf.set_font("Helvetica", "", 9)
        pdf.multi_cell(45, 5, safe(waarde))

    # Regels.
    pdf.set_y(max(pdf.get_y(), 82))
    pdf.set_x(15)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(96, 7, "Omschrijving", border="B")
    pdf.cell(20, 7, "Aantal", border="B", align="R")
    pdf.cell(28, 7, "Per stuk", border="B", align="R")
    pdf.cell(36, 7, "Bedrag", border="B", align="R", new_x="LMARGIN", new_y="NEXT")

    periode = ""
    if f.periode_van and f.periode_tot:
        periode = (f" ({f.periode_van.strftime('%d-%m-%Y')} t/m "
                   f"{f.periode_tot.strftime('%d-%m-%Y')})")
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(96, 7, safe("FieldOps abonnement" + periode))
    pdf.cell(20, 7, str(f.seats), align="R")
    pdf.cell(28, 7, safe(f"EUR {f.tarief_excl}"), align="R")
    pdf.cell(36, 7, safe(f"EUR {f.bedrag_excl}"), align="R",
             new_x="LMARGIN", new_y="NEXT")

    def totaalregel(label, bedrag, vet=False):
        pdf.set_x(111)
        pdf.set_font("Helvetica", "B" if vet else "", 9.5)
        pdf.cell(69, 6.5, safe(label))
        pdf.cell(0, 6.5, safe(f"EUR {bedrag}"), align="R",
                 new_x="LMARGIN", new_y="NEXT")

    pdf.ln(2)
    totaalregel("Subtotaal excl. BTW", f.bedrag_excl)
    totaalregel(f"BTW {f.btw_percentage}%", f.btw_bedrag)
    pdf.set_x(111)
    pdf.line(111, pdf.get_y(), 195, pdf.get_y())
    pdf.ln(1)
    totaalregel("Totaal", f.bedrag_incl, vet=True)

    pdf.ln(8)
    pdf.set_x(15)
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(180, 4.6, safe(
        "Dit bedrag is automatisch geincasseerd via SEPA-incasso; u hoeft niets "
        "te doen. Klopt er iets niet, neem dan binnen acht weken contact op -- "
        "een SEPA-incasso is tot die termijn terug te boeken via uw bank."))
    pdf.set_text_color(0, 0, 0)

    return bytes(pdf.output())
