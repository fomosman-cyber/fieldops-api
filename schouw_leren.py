"""Leren van de eigen ritten: bevestigde en afgewezen schades als voorbeelden.

Een model dat Nederlandse straten niet kent, ziet een naad tussen twee
asfaltbanen voor een scheur en een reparatievlak voor schade. De inspecteur
corrigeert dat na de rit. Die oordelen komen hier terug als voorbeelden bij
elk volgend beeld: een uitsnede van de schade, met wat de inspecteur ervan
vond. Zo leert de herkenning per organisatie van haar eigen wegen, zonder
een eigen model te trainen.

- **Alleen oordelen van mensen.** Een waarneming telt als voorbeeld als iemand
  hem heeft bevestigd, gecorrigeerd of afgewezen.
- **De fouten wegen het zwaarst.** Een afgewezen schade leert het model meer
  dan de zoveelste bevestigde kuil; afgewezen voorbeelden gaan voor.
- **Goedkoop.** De voorbeelden staan vóór het beeld en worden gecachet: na de
  eerste aanroep kosten ze een tiende. Ze worden per organisatie een kwartier
  onthouden, en opnieuw gemaakt zodra er een oordeel bij komt.
"""
from __future__ import annotations

import base64
import io
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

AFWIJS_REDENEN = {
    "wegmarkering": "Wegmarkering of belijning",
    "schaduw": "Schaduw",
    "nat": "Nat wegdek of plas",
    "reparatie": "Reparatievlak zonder schade",
    "putdeksel": "Putdeksel, kolk of rooster",
    "naad": "Naad of aansluiting",
    "vuil": "Blad, zand of vuil op de weg",
    "anders": "Iets anders",
}

MAX_AFGEWEZEN = 4
MAX_BEVESTIGD = 4
VENSTER_DAGEN = 120
BEWAAR_S = 15 * 60
UITSNEDE_PX = 384

_cache: dict[str, tuple[float, list]] = {}
_slot = threading.Lock()


def vergeet(organization_id: str) -> None:
    """Na een nieuw oordeel: de voorbeelden opnieuw samenstellen."""
    with _slot:
        _cache.pop(organization_id, None)


def voorbeelden(db, organization_id: str) -> list[dict]:
    """De voorbeelden voor één organisatie, als inhoudsblokken die vóór het
    te beoordelen beeld gaan. Leeg als er nog niets beoordeeld is."""
    nu = time.monotonic()
    with _slot:
        bewaard = _cache.get(organization_id)
        if bewaard and nu - bewaard[0] < BEWAAR_S:
            return bewaard[1]
    blokken = _maak(db, organization_id)
    with _slot:
        _cache[organization_id] = (nu, blokken)
    return blokken


def _maak(db, organization_id: str) -> list[dict]:
    import crow_wegschade as cw
    from models import Schouwwaarneming

    sinds = datetime.now(timezone.utc) - timedelta(days=VENSTER_DAGEN)
    rijen = (db.query(Schouwwaarneming)
               .filter(Schouwwaarneming.organization_id == organization_id,
                       Schouwwaarneming.crow_schadebeeld.isnot(None),
                       Schouwwaarneming.bron == "ai",
                       Schouwwaarneming.photo_url.isnot(None),
                       Schouwwaarneming.kader.isnot(None),
                       (Schouwwaarneming.bevestigd.is_(True)) | (Schouwwaarneming.afgewezen.is_(True)),
                       Schouwwaarneming.created_at >= sinds)
               .order_by(Schouwwaarneming.created_at.desc())
               .limit(300).all())
    if not rijen:
        return []

    def verspreid(lijst, n):
        """Zoveel mogelijk verschillende schadebeelden, nieuwste eerst."""
        gekozen, gezien = [], set()
        for w in lijst:
            if w.crow_schadebeeld not in gezien:
                gekozen.append(w)
                gezien.add(w.crow_schadebeeld)
        for w in lijst:
            if len(gekozen) >= n:
                break
            if w not in gekozen:
                gekozen.append(w)
        return gekozen[:n]

    afgewezen = verspreid([w for w in rijen if w.afgewezen], MAX_AFGEWEZEN)
    bevestigd = verspreid([w for w in rijen if w.bevestigd], MAX_BEVESTIGD)

    blokken: list[dict] = []
    for w, goed in [(w, False) for w in afgewezen] + [(w, True) for w in bevestigd]:
        beeld = _uitsnede(w.photo_url, w.kader)
        if beeld is None:
            continue
        naam = (cw.zoek(w.crow_verharding, w.crow_schadebeeld) or {}).get("naam", w.crow_schadebeeld)
        if goed:
            onderschrift = f"BEVESTIGD door de inspecteur: {naam} ({w.crow_schadebeeld}), ernst {w.crow_ernst or '?'}."
            if w.oorspronkelijk_schadebeeld and w.oorspronkelijk_schadebeeld != w.crow_schadebeeld:
                onderschrift += f" De herkenning zei eerst {w.oorspronkelijk_schadebeeld}; dat was fout."
        else:
            reden = AFWIJS_REDENEN.get(w.afwijs_reden or "", "")
            onderschrift = (f"AFGEWEZEN: de herkenning meldde {naam} ({w.crow_schadebeeld}), "
                            f"maar dit is geen schade" + (f": {reden.lower()}." if reden else "."))
        blokken += [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": beeld}},
                    {"type": "text", "text": onderschrift}]
    if not blokken:
        return []

    lessen = _lessen(rijen)
    inleiding = ("VOORBEELDEN uit eerdere schouwen van deze organisatie, beoordeeld door een "
                 "inspecteur. Elk voorbeeld is een uitsnede rond wat de herkenning meldde. "
                 "Leer vooral van de afgewezen voorbeelden: meld zoiets niet opnieuw als schade.")
    if lessen:
        inleiding += "\n" + lessen
    blokken.insert(0, {"type": "text", "text": inleiding})
    blokken.append({"type": "text", "text": "Einde van de voorbeelden. Hierna volgt het beeld dat je beoordeelt.",
                    "cache_control": {"type": "ephemeral"}})
    return blokken


def _lessen(rijen) -> str:
    """Wat er vaak ten onrechte werd gemeld, in één paar regels."""
    tel: dict[tuple[str, str], int] = {}
    for w in rijen:
        if w.afgewezen and w.afwijs_reden and w.afwijs_reden != "anders":
            sleutel = (w.crow_schadebeeld, w.afwijs_reden)
            tel[sleutel] = tel.get(sleutel, 0) + 1
    if not tel:
        return ""
    regels = [f"- {sb} gemeld, maar het was {AFWIJS_REDENEN[r].lower()} ({n}x)"
              for (sb, r), n in sorted(tel.items(), key=lambda x: -x[1])[:6]]
    return "Vaak ten onrechte gemeld bij deze organisatie:\n" + "\n".join(regels)


def _uitsnede(photo_url: str, kader_tekst: str) -> Optional[str]:
    """De schade met wat omgeving eromheen, klein genoeg om goedkoop te zijn."""
    import json

    import photo_storage
    from PIL import Image

    try:
        kader = json.loads(kader_tekst)
        data = photo_storage.lees_foto(photo_url)
        if not data or not isinstance(kader, list) or len(kader) != 4:
            return None
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            b, h = im.size
            x0, y0, x1, y1 = kader
            mx, my = (x1 - x0) * 0.4 + 0.03, (y1 - y0) * 0.4 + 0.03
            vak = (int(max(0.0, x0 - mx) * b), int(max(0.0, y0 - my) * h),
                   int(min(1.0, x1 + mx) * b), int(min(1.0, y1 + my) * h))
            if vak[2] - vak[0] < 8 or vak[3] - vak[1] < 8:
                return None
            uit = im.crop(vak)
            uit.thumbnail((UITSNEDE_PX, UITSNEDE_PX))
            buf = io.BytesIO()
            uit.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception:  # noqa: BLE001 — één kapot voorbeeld mag de rest niet tegenhouden
        return None
