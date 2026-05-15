"""i18n-laag voor kunstwerken / VTA / speeltoestel / verlichting taxonomy.

Vertaalt de meest-getoonde labels (element-namen, groep-labels, scoring-
labels, top-15 vraag-keys) naar EN/DE/FR/TR. Detail-uitleg blijft in
de bron-taal (NL) tenzij een vertaler die later overzet — dat is bewust:
juridisch-precieze norm-tekst hoort niet door een ML-translator gepoept.

Frontend roept `GET /api/kunstwerken-inspecties/i18n/{lang}` om de
label-map voor een bepaalde taal op te halen.
"""
from __future__ import annotations
from typing import Optional


# ─────────────────────────────────────────────────────────────────────────────
# Element-namen per taal
# ─────────────────────────────────────────────────────────────────────────────

ELEMENT_NAMES = {
    "en": {
        # Bruggen
        "BRUG.ONDERBOUW": "Substructure / abutments",
        "BRUG.PIJLERS": "Piers / columns",
        "BRUG.BOVENBOUW": "Superstructure",
        "BRUG.BRUGDEK": "Bridge deck",
        "BRUG.VOEGOVERGANGEN": "Expansion joints",
        "BRUG.OPLEGGINGEN": "Bearings",
        "BRUG.LEUNINGEN": "Railings",
        "BRUG.AFWATERING": "Drainage",
        "BRUG.OEVER": "Bank / slope protection",
        "BRUG.VERLICHTING": "Lighting on structure",
        # Bomen
        "BOOM.STAM": "Trunk",
        "BOOM.WORTELAANLOOP": "Root flare",
        "BOOM.HOOFDTAKKEN": "Main / scaffold branches",
        "BOOM.KROON": "Crown / canopy",
        "BOOM.STANDPLAATS": "Growing site",
        # Speeltoestel
        "SPEEL.CONSTRUCTIE": "Frame / structure",
        "SPEEL.BEWEGENDE_DELEN": "Moving parts",
        "SPEEL.KLIMDELEN": "Climbing / grip surfaces",
        "SPEEL.VALDEMPING": "Fall-impact surface",
        "SPEEL.VEILIGHEID": "Safety zones + clearances",
        # Verlichting
        "OV.MAST": "Lamp post",
        "OV.ARMATUUR": "Luminaire",
        "OV.AANSLUITKAST": "Connection cabinet",
        "OV.AARDING": "Earthing / RCD",
    },
    "de": {
        "BRUG.ONDERBOUW": "Unterbau / Widerlager",
        "BRUG.PIJLERS": "Pfeiler / Stützen",
        "BRUG.BOVENBOUW": "Überbau",
        "BRUG.BRUGDEK": "Brückendeck",
        "BRUG.VOEGOVERGANGEN": "Dehnfugen",
        "BRUG.OPLEGGINGEN": "Lager",
        "BRUG.LEUNINGEN": "Geländer",
        "BRUG.AFWATERING": "Entwässerung",
        "BRUG.OEVER": "Ufer-/Böschungsschutz",
        "BRUG.VERLICHTUNG": "Beleuchtung am Bauwerk",
        "BOOM.STAM": "Stamm",
        "BOOM.WORTELAANLOOP": "Wurzelanlauf",
        "BOOM.HOOFDTAKKEN": "Hauptäste / Leitäste",
        "BOOM.KROON": "Krone",
        "BOOM.STANDPLAATS": "Standort",
        "SPEEL.CONSTRUCTIE": "Konstruktion",
        "SPEEL.BEWEGENDE_DELEN": "Bewegliche Teile",
        "SPEEL.KLIMDELEN": "Kletterelemente",
        "SPEEL.VALDEMPING": "Fallschutz-Boden",
        "SPEEL.VEILIGHEID": "Sicherheitsabstände",
        "OV.MAST": "Mast",
        "OV.ARMATUUR": "Leuchte",
        "OV.AANSLUITKAST": "Anschlusskasten",
        "OV.AARDING": "Erdung / RCD",
    },
    "fr": {
        "BRUG.ONDERBOUW": "Infrastructure / culées",
        "BRUG.PIJLERS": "Piles / colonnes",
        "BRUG.BOVENBOUW": "Superstructure",
        "BRUG.BRUGDEK": "Tablier",
        "BRUG.VOEGOVERGANGEN": "Joints de dilatation",
        "BRUG.OPLEGGINGEN": "Appareils d'appui",
        "BRUG.LEUNINGEN": "Garde-corps",
        "BRUG.AFWATERING": "Drainage",
        "BRUG.OEVER": "Protection de berges",
        "BRUG.VERLICHTING": "Éclairage sur ouvrage",
        "BOOM.STAM": "Tronc",
        "BOOM.WORTELAANLOOP": "Empattement racinaire",
        "BOOM.HOOFDTAKKEN": "Branches charpentières",
        "BOOM.KROON": "Couronne",
        "BOOM.STANDPLAATS": "Site de plantation",
        "SPEEL.CONSTRUCTIE": "Structure portante",
        "SPEEL.BEWEGENDE_DELEN": "Pièces mobiles",
        "SPEEL.KLIMDELEN": "Surfaces d'escalade",
        "SPEEL.VALDEMPING": "Sol amortissant",
        "SPEEL.VEILIGHEID": "Distances de sécurité",
        "OV.MAST": "Mât d'éclairage",
        "OV.ARMATUUR": "Luminaire",
        "OV.AANSLUITKAST": "Coffret de raccordement",
        "OV.AARDING": "Mise à la terre / DDR",
    },
    "tr": {
        "BRUG.ONDERBOUW": "Alt yapı / kenar ayakları",
        "BRUG.PIJLERS": "Köprü ayakları",
        "BRUG.BOVENBOUW": "Üst yapı",
        "BRUG.BRUGDEK": "Köprü tabliyesi",
        "BRUG.VOEGOVERGANGEN": "Genleşme derzleri",
        "BRUG.OPLEGGINGEN": "Mesnetler",
        "BRUG.LEUNINGEN": "Korkuluklar",
        "BRUG.AFWATERING": "Drenaj",
        "BRUG.OEVER": "Şev / kıyı koruma",
        "BRUG.VERLICHTING": "Yapı aydınlatması",
        "BOOM.STAM": "Gövde",
        "BOOM.WORTELAANLOOP": "Kök boğazı",
        "BOOM.HOOFDTAKKEN": "Ana dallar",
        "BOOM.KROON": "Taç",
        "BOOM.STANDPLAATS": "Yetişme ortamı",
        "SPEEL.CONSTRUCTIE": "Yapı",
        "SPEEL.BEWEGENDE_DELEN": "Hareketli parçalar",
        "SPEEL.KLIMDELEN": "Tırmanma yüzeyleri",
        "SPEEL.VALDEMPING": "Darbe emici zemin",
        "SPEEL.VEILIGHEID": "Güvenlik mesafeleri",
        "OV.MAST": "Aydınlatma direği",
        "OV.ARMATUUR": "Armatür",
        "OV.AANSLUITKAST": "Bağlantı kutusu",
        "OV.AARDING": "Topraklama / kaçak akım",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Groep-labels per taal
# ─────────────────────────────────────────────────────────────────────────────

GROEP_LABELS = {
    "en": {
        "constructief": "Structural", "afwerking": "Finishing",
        "installatie": "Equipment", "omgeving": "Environment",
        "vegetatief": "Vegetation", "speel_constructief": "Play structure",
        "speel_veiligheid": "Play safety", "ov_elektrisch": "Electrical (OV)",
    },
    "de": {
        "constructief": "Konstruktiv", "afwerking": "Oberflächen",
        "installatie": "Anlage", "omgeving": "Umfeld",
        "vegetatief": "Vegetation", "speel_constructief": "Spielkonstruktion",
        "speel_veiligheid": "Spielsicherheit", "ov_elektrisch": "Elektrisch (Beleuchtung)",
    },
    "fr": {
        "constructief": "Structurel", "afwerking": "Finitions",
        "installatie": "Équipement", "omgeving": "Environnement",
        "vegetatief": "Végétation", "speel_constructief": "Structure jeu",
        "speel_veiligheid": "Sécurité jeu", "ov_elektrisch": "Électrique (éclairage)",
    },
    "tr": {
        "constructief": "Yapısal", "afwerking": "Yüzey",
        "installatie": "Donanım", "omgeving": "Çevre",
        "vegetatief": "Bitki örtüsü", "speel_constructief": "Oyun yapısı",
        "speel_veiligheid": "Oyun güvenliği", "ov_elektrisch": "Elektrik (aydınlatma)",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Kern-vraag labels per taal (top 15 meest-gebruikte vragen)
# ─────────────────────────────────────────────────────────────────────────────

QUESTION_LABELS = {
    "en": {
        "GEN.STAAT": "General visual condition of this element",
        "GEN.VEILIG": "Safety risk identified?",
        "GEN.INSPECTEERBAARHEID": "Was full inspection possible?",
        "GEN.URGENTIE": "Maintenance urgency",
        "CONSTR.SCHEUR": "Cracks > 0.2 mm visible in main material?",
        "CONSTR.WAPENING": "Reinforcement corrosion or cover deficit?",
        "CONSTR.VERVORM": "Abnormal deformation or tilt?",
        "VEG.SCHEUR": "Visible cracks in woody tissue?",
        "VEG.SCHEEF": "Abnormal lean or tilt?",
        "VTA.STAM_HOLTE_PCT": "Estimated trunk-cavity percentage",
        "VTA.STAM_ZWAM": "Wood-decay fungus present?",
        "VTA.VITALITEIT": "NTS vitality class",
        "SPEEL.STABIEL": "Toy mechanically stable under load?",
        "SPEEL.KNELPUNT_GETEST": "Entrapment-zone test performed?",
        "OV.AARDING_OK": "Earthing resistance within norm (< 100Ω)?",
    },
    "de": {
        "GEN.STAAT": "Allgemeiner Sichtzustand dieses Elements",
        "GEN.VEILIG": "Sicherheitsrisiko festgestellt?",
        "GEN.INSPECTEERBAARHEID": "War die Inspektion vollständig möglich?",
        "GEN.URGENTIE": "Instandhaltungs-Dringlichkeit",
        "CONSTR.SCHEUR": "Risse > 0,2 mm im Hauptwerkstoff sichtbar?",
        "CONSTR.WAPENING": "Bewehrungs-Korrosion oder Deckungsdefizit?",
        "CONSTR.VERVORM": "Abweichende Verformung oder Schiefstand?",
        "VEG.SCHEUR": "Sichtbare Risse im Holzgewebe?",
        "VEG.SCHEEF": "Abweichende Schiefstand?",
        "VTA.STAM_HOLTE_PCT": "Geschätzter Stamm-Hohlraum-Anteil",
        "VTA.STAM_ZWAM": "Holzzerstörender Pilz vorhanden?",
        "VTA.VITALITEIT": "Vitalitätsklasse nach NTS",
        "SPEEL.STABIEL": "Spielgerät mechanisch stabil unter Last?",
        "SPEEL.KNELPUNT_GETEST": "Klemmstellen-Test durchgeführt?",
        "OV.AARDING_OK": "Erdungswiderstand im Normbereich (< 100Ω)?",
    },
    "fr": {
        "GEN.STAAT": "État visuel général de cet élément",
        "GEN.VEILIG": "Risque de sécurité constaté ?",
        "GEN.INSPECTEERBAARHEID": "Inspection complète possible ?",
        "GEN.URGENTIE": "Urgence d'entretien",
        "CONSTR.SCHEUR": "Fissures > 0,2 mm dans le matériau principal ?",
        "CONSTR.WAPENING": "Corrosion d'armature ou défaut d'enrobage ?",
        "CONSTR.VERVORM": "Déformation ou inclinaison anormale ?",
        "VEG.SCHEUR": "Fissures visibles dans le bois ?",
        "VEG.SCHEEF": "Inclinaison ou bascule anormale ?",
        "VTA.STAM_HOLTE_PCT": "Pourcentage estimé de cavité du tronc",
        "VTA.STAM_ZWAM": "Champignon lignivore présent ?",
        "VTA.VITALITEIT": "Classe de vitalité NTS",
        "SPEEL.STABIEL": "Jeu stable mécaniquement sous charge ?",
        "SPEEL.KNELPUNT_GETEST": "Test des zones de piégeage effectué ?",
        "OV.AARDING_OK": "Résistance de mise à la terre conforme (< 100Ω) ?",
    },
    "tr": {
        "GEN.STAAT": "Bu elemanın genel görsel durumu",
        "GEN.VEILIG": "Güvenlik riski tespit edildi mi?",
        "GEN.INSPECTEERBAARHEID": "Tam denetim mümkün müydü?",
        "GEN.URGENTIE": "Bakım aciliyeti",
        "CONSTR.SCHEUR": "Ana malzemede > 0,2 mm çatlak görünür mü?",
        "CONSTR.WAPENING": "Donatı korozyonu veya paspayı eksikliği?",
        "CONSTR.VERVORM": "Anormal deformasyon veya eğiklik?",
        "VEG.SCHEUR": "Odunsu dokuda görünür çatlak?",
        "VEG.SCHEEF": "Anormal eğiklik veya devrilme?",
        "VTA.STAM_HOLTE_PCT": "Tahmini gövde-boşluk yüzdesi",
        "VTA.STAM_ZWAM": "Odun çürüten mantar var mı?",
        "VTA.VITALITEIT": "NTS canlılık sınıfı",
        "SPEEL.STABIEL": "Oyun aleti yük altında mekanik olarak sabit mi?",
        "SPEEL.KNELPUNT_GETEST": "Sıkışma noktası testi yapıldı mı?",
        "OV.AARDING_OK": "Topraklama direnci norm aralığında mı (< 100Ω)?",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Type-labels per taal (KUNSTWERK_TYPES vertalingen)
# ─────────────────────────────────────────────────────────────────────────────

TYPE_LABELS = {
    "en": {
        "brug": "Bridge", "viaduct": "Viaduct", "tunnel": "Tunnel",
        "sluis": "Lock", "stuw": "Weir", "duiker": "Culvert",
        "kademuur": "Quay wall", "gemaal": "Pumping station",
        "riolering": "Sewerage", "boom": "Tree (VTA)",
        "speeltoestel": "Playground (NEN-EN 1176)",
        "verlichting": "Public lighting (NEN 3140)",
        "fontein": "Fountain", "kunstgrasveld": "Artificial turf field",
        "wegmarkering": "Road marking (CROW 145)",
    },
    "de": {
        "brug": "Brücke", "viaduct": "Viadukt", "tunnel": "Tunnel",
        "sluis": "Schleuse", "stuw": "Wehr", "duiker": "Durchlass",
        "kademuur": "Kaimauer", "gemaal": "Pumpwerk",
        "riolering": "Kanalisation", "boom": "Baum (VTA)",
        "speeltoestel": "Spielgerät (EN 1176)",
        "verlichting": "Straßenbeleuchtung (DIN 0105)",
        "fontein": "Brunnen", "kunstgrasveld": "Kunstrasenplatz",
        "wegmarkering": "Fahrbahnmarkierung",
    },
    "fr": {
        "brug": "Pont", "viaduct": "Viaduc", "tunnel": "Tunnel",
        "sluis": "Écluse", "stuw": "Barrage", "duiker": "Buse / dalot",
        "kademuur": "Mur de quai", "gemaal": "Station de pompage",
        "riolering": "Assainissement", "boom": "Arbre (VTA)",
        "speeltoestel": "Jeu (EN 1176)",
        "verlichting": "Éclairage public",
        "fontein": "Fontaine", "kunstgrasveld": "Terrain synthétique",
        "wegmarkering": "Marquage routier",
    },
    "tr": {
        "brug": "Köprü", "viaduct": "Viyadük", "tunnel": "Tünel",
        "sluis": "Kilit", "stuw": "Bent", "duiker": "Menfez",
        "kademuur": "Rıhtım duvarı", "gemaal": "Pompa istasyonu",
        "riolering": "Kanalizasyon", "boom": "Ağaç (VTA)",
        "speeltoestel": "Oyun grubu (EN 1176)",
        "verlichting": "Yol aydınlatması",
        "fontein": "Çeşme", "kunstgrasveld": "Sentetik çim saha",
        "wegmarkering": "Yol işaretleme",
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

SUPPORTED_LANGS = {"nl", "en", "de", "fr", "tr"}


def get_i18n_bundle(lang: str) -> dict:
    """Returnt het hele i18n-bundle voor één taal.

    Frontend roept dit één keer bij taal-switch op en mergt de label-map
    over de bestaande data van /taxonomy endpoints.

    Voor 'nl' wordt een lege dict teruggegeven — de bron-taal heeft geen
    overrides nodig.
    """
    lang = (lang or "nl").lower()[:2]
    if lang == "nl" or lang not in SUPPORTED_LANGS:
        return {"lang": "nl", "elements": {}, "groups": {},
                "questions": {}, "types": {}}
    return {
        "lang": lang,
        "elements": ELEMENT_NAMES.get(lang, {}),
        "groups": GROEP_LABELS.get(lang, {}),
        "questions": QUESTION_LABELS.get(lang, {}),
        "types": TYPE_LABELS.get(lang, {}),
    }


I18N_VERSION = "kw-i18n.v1.0-2026-05"
