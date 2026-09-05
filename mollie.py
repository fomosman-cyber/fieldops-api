"""Dunne Mollie-client voor de abonnementen van FieldOps.

Opzet en beperkingen zijn gebaseerd op de Mollie-documentatie zoals
gecontroleerd op 4 september 2026:

* Een abonnement kan pas bestaan als er een **mandaat** is. Dat mandaat
  ontstaat automatisch uit een eerste betaling met ``sequenceType="first"``.
  iDEAL kan zelf geen incasso, maar levert wél een ``directdebit``-mandaat op
  het IBAN van die eerste betaling. Daarom rekenen we één cent af om het
  mandaat te krijgen, niet de eerste maand.
* Abonnementen accepteren een mandaat met status ``pending`` óf ``valid``;
  losse ``recurring``-betalingen vereisen ``valid``.
* Het bedrag van een lopend abonnement is te wijzigen met een ``PATCH``. Er is
  géén verrekening naar rato in Mollie — een gewijzigd bedrag geldt vanaf de
  eerstvolgende incasso. Wie halverwege de maand gebruikers bijzet, betaalt die
  dus vanaf de volgende termijn.
* ``description`` moet uniek zijn per klant zolang er meerdere actieve
  abonnementen zijn. We zetten het organisatie-id erin.

**Fail-closed.** Zonder ``MOLLIE_API_KEY`` doet deze module niets en gooit
:class:`MollieNietGeconfigureerd`. Dat is bewust: stilzwijgend niets doen zou
betekenen dat een klant denkt betaald te hebben terwijl er geen abonnement is.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

API_BASE = "https://api.mollie.com/v2"
_TIMEOUT = 15.0


class MollieNietGeconfigureerd(RuntimeError):
    """Er is geen API-sleutel ingesteld op deze omgeving."""


class MollieFout(RuntimeError):
    """Mollie gaf een foutantwoord terug."""

    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Mollie {status_code}: {detail}")


def api_key() -> str:
    """Sleutel op aanroeptijd lezen, niet cachen.

    Zo werkt een gewijzigde omgevingsvariabele meteen door en kunnen tests de
    sleutel per geval zetten, net als bij de Shopify-integratie.
    """
    return (os.getenv("MOLLIE_API_KEY") or "").strip()


def is_geconfigureerd() -> bool:
    return bool(api_key())


def is_testmodus() -> bool:
    """True bij een ``test_``-sleutel. Alleen voor weergave in het portaal."""
    return api_key().startswith("test_")


def _verzoek(methode: str, pad: str, payload: Optional[dict] = None) -> dict[str, Any]:
    sleutel = api_key()
    if not sleutel:
        raise MollieNietGeconfigureerd(
            "MOLLIE_API_KEY ontbreekt op deze omgeving")

    url = f"{API_BASE}{pad}"
    kop = {
        "Authorization": f"Bearer {sleutel}",
        "Content-Type": "application/json",
        "User-Agent": "FieldOps/1.0",
    }
    try:
        with httpx.Client(timeout=_TIMEOUT) as cli:
            antwoord = cli.request(methode, url, headers=kop, json=payload)
    except httpx.HTTPError as exc:
        raise MollieFout(502, f"Mollie niet bereikbaar: {exc}") from exc

    if antwoord.status_code >= 400:
        detail = antwoord.text[:400]
        try:
            body = antwoord.json()
            detail = body.get("detail") or body.get("title") or detail
        except Exception:  # noqa: BLE001 — foutbody hoeft geen JSON te zijn
            pass
        raise MollieFout(antwoord.status_code, detail)

    if not antwoord.content:
        return {}
    return antwoord.json()


# --------------------------------------------------------------------------
# Klanten
# --------------------------------------------------------------------------

def maak_customer(naam: str, email: str, metadata: Optional[dict] = None) -> dict:
    return _verzoek("POST", "/customers", {
        "name": naam[:255],
        "email": email,
        "locale": "nl_NL",
        "metadata": metadata or {},
    })


def haal_customer(customer_id: str) -> dict:
    return _verzoek("GET", f"/customers/{customer_id}")


# --------------------------------------------------------------------------
# Betalingen
# --------------------------------------------------------------------------

def maak_eerste_betaling(
    customer_id: str,
    *,
    bedrag: str,
    beschrijving: str,
    redirect_url: str,
    webhook_url: str,
    methode: Optional[str] = "ideal",
    metadata: Optional[dict] = None,
) -> dict:
    """Eerste betaling die het incassomandaat oplevert.

    ``bedrag`` is een string met twee decimalen ("0.01"), zoals Mollie eist.
    """
    payload: dict[str, Any] = {
        "amount": {"currency": "EUR", "value": bedrag},
        "description": beschrijving[:255],
        "redirectUrl": redirect_url,
        "webhookUrl": webhook_url,
        "sequenceType": "first",
        "customerId": customer_id,
        "locale": "nl_NL",
        "metadata": metadata or {},
    }
    if methode:
        payload["method"] = methode
    return _verzoek("POST", "/payments", payload)


def haal_betaling(payment_id: str) -> dict:
    """Betaling ophalen bij Mollie.

    Dit is tegelijk de verificatie van de webhook: Mollie ondertekent zijn
    webhooks niet, dus het enige dat we vertrouwen is wat de API zelf zegt.
    """
    return _verzoek("GET", f"/payments/{payment_id}")


# --------------------------------------------------------------------------
# Mandaten
# --------------------------------------------------------------------------

def lijst_mandaten(customer_id: str) -> list[dict]:
    data = _verzoek("GET", f"/customers/{customer_id}/mandates?limit=250")
    embedded = data.get("_embedded") or {}
    return embedded.get("mandates") or []


def eerste_bruikbare_mandaat(customer_id: str) -> Optional[dict]:
    """Mandaat dat een abonnement mag dragen.

    Abonnementen accepteren ``pending`` en ``valid``; ``valid`` heeft voorrang
    omdat daar zeker een IBAN aan hangt.
    """
    mandaten = lijst_mandaten(customer_id)
    for gewenst in ("valid", "pending"):
        for m in mandaten:
            if m.get("status") == gewenst:
                return m
    return None


# --------------------------------------------------------------------------
# Abonnementen
# --------------------------------------------------------------------------

def maak_abonnement(
    customer_id: str,
    *,
    bedrag: str,
    beschrijving: str,
    webhook_url: str,
    interval: str = "1 month",
    start_datum: Optional[str] = None,
    mandate_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> dict:
    payload: dict[str, Any] = {
        "amount": {"currency": "EUR", "value": bedrag},
        "interval": interval,
        "description": beschrijving[:255],
        "webhookUrl": webhook_url,
        "metadata": metadata or {},
    }
    if start_datum:
        payload["startDate"] = start_datum
    if mandate_id:
        payload["mandateId"] = mandate_id
    return _verzoek("POST", f"/customers/{customer_id}/subscriptions", payload)


def haal_abonnement(customer_id: str, subscription_id: str) -> dict:
    return _verzoek(
        "GET", f"/customers/{customer_id}/subscriptions/{subscription_id}")


def wijzig_abonnement(customer_id: str, subscription_id: str, **velden) -> dict:
    """Bedrag of andere velden van een lopend abonnement aanpassen.

    Let op: opgezegde abonnementen kunnen niet gewijzigd worden, en Mollie
    verrekent niets naar rato — het nieuwe bedrag geldt vanaf de volgende
    incasso.
    """
    payload: dict[str, Any] = {}
    if "bedrag" in velden and velden["bedrag"] is not None:
        payload["amount"] = {"currency": "EUR", "value": velden["bedrag"]}
    for van, naar in (("beschrijving", "description"),
                      ("interval", "interval"),
                      ("webhook_url", "webhookUrl"),
                      ("mandate_id", "mandateId")):
        if velden.get(van) is not None:
            payload[naar] = velden[van]
    if not payload:
        return haal_abonnement(customer_id, subscription_id)
    return _verzoek(
        "PATCH", f"/customers/{customer_id}/subscriptions/{subscription_id}",
        payload)


def zeg_abonnement_op(customer_id: str, subscription_id: str) -> dict:
    """Abonnement opzeggen.

    Het mandaat blijft daarna geldig; een klant die terugkomt hoeft dus niet
    opnieuw een eerste betaling te doen.
    """
    return _verzoek(
        "DELETE", f"/customers/{customer_id}/subscriptions/{subscription_id}")
