"""SEO-endpoints voor portaal.fieldopsapp.nl.

  GET /robots.txt   — verwijst crawlers naar de sitemap
  GET /sitemap.xml  — bevat alle publieke pagina's met hreflang per taal

De portaal-host is primair een admin-portal (`/portaal`) — de meerwaarde van
SEO zit in de publieke landing-pagina's: developer-portal, whitepaper-CTA en
de api-root als entry point. Voor Google-zichtbaarheid op NL infra-zoekopdrachten
moet de apex-site (fieldopsapp.nl) zelf ook structured data hebben; deze
sitemap exposeert alleen wat er op deze host live staat.
"""

from __future__ import annotations
from datetime import datetime, timezone
import os

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter(tags=["SEO"])


# Production-host voor canonical/og links. Op Render zit de service achter
# portaal.fieldopsapp.nl; lokaal kan dit via env worden overschreven.
DEFAULT_PUBLIC_HOST = "https://portaal.fieldopsapp.nl"

# Volgorde van talen die we als alternates op publieke pagina's exposen.
# Eerste = x-default. Komt overeen met de in-portaal taalkeuze.
SUPPORTED_LANGUAGES = ["nl", "en", "de", "fr", "tr"]


def public_host() -> str:
    """Geef de canonical host terug. Render zet RENDER_EXTERNAL_URL automatisch
    op de service-URL; in productie overschrijven we naar het apex-domein via
    PUBLIC_HOST env."""
    host = os.getenv("PUBLIC_HOST") or os.getenv("RENDER_EXTERNAL_URL") or DEFAULT_PUBLIC_HOST
    return host.rstrip("/")


# Statische lijst met publieke routes die in de sitemap horen. Volgorde wordt
# gebruikt door zowel sitemap.xml als interne navigatie-helpers.
# (loc_path, changefreq, priority, has_lang_alternates)
PUBLIC_ROUTES: list[tuple[str, str, str, bool]] = [
    ("/",            "weekly",  "0.9", False),
    ("/developers",  "weekly",  "0.8", False),
    ("/whitepaper",  "monthly", "0.7", False),
    ("/portaal",     "weekly",  "0.5", True),   # taal-aware UI
]


@router.get("/robots.txt", include_in_schema=False)
def robots_txt(request: Request) -> Response:
    """Crawler-policy. Sluit alle /api-routes uit (privé) maar laat publieke
    pagina's en de sitemap door. Zoekmachines moeten aan de portaal-pagina's
    kunnen indexeren voor merknaam-zichtbaarheid."""
    host = public_host()
    body = (
        "User-agent: *\n"
        "Disallow: /api/\n"
        "Disallow: /openapi.json\n"
        "Disallow: /docs\n"
        "Disallow: /redoc\n"
        "Disallow: /reset-wachtwoord\n"
        "Allow: /\n"
        "\n"
        f"Sitemap: {host}/sitemap.xml\n"
    )
    return Response(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/sitemap.xml", include_in_schema=False)
def sitemap_xml(request: Request) -> Response:
    """XML-sitemap conform sitemaps.org schema. Voor /portaal voegen we
    xhtml:link rel="alternate" hreflang per taal toe — Google gebruikt dat om
    de juiste taalvariant per regio te tonen."""
    host = public_host()
    today = datetime.now(timezone.utc).date().isoformat()

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"',
        '        xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]

    for path, changefreq, priority, has_lang_alts in PUBLIC_ROUTES:
        loc = f"{host}{path}"
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append(f"    <lastmod>{today}</lastmod>")
        parts.append(f"    <changefreq>{changefreq}</changefreq>")
        parts.append(f"    <priority>{priority}</priority>")
        if has_lang_alts:
            for lang in SUPPORTED_LANGUAGES:
                parts.append(
                    f'    <xhtml:link rel="alternate" hreflang="{lang}" '
                    f'href="{loc}?lang={lang}"/>'
                )
            parts.append(
                f'    <xhtml:link rel="alternate" hreflang="x-default" href="{loc}"/>'
            )
        parts.append("  </url>")

    parts.append("</urlset>")
    body = "\n".join(parts) + "\n"

    return Response(
        content=body,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )
