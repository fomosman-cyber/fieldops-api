"""SEO-endpoints voor portaal.fieldopsapp.nl.

  GET /robots.txt   — verwijst crawlers naar de sitemap
  GET /sitemap.xml  — alleen indexeerbare publieke pagina's

De portaal-host is primair een admin-portal (`/portaal`) — de meerwaarde van
SEO zit in de publieke landing-pagina's: developer-portal, whitepaper-CTA en
de api-root als entry point. Voor Google-zichtbaarheid op NL infra-zoekopdrachten
moet de apex-site (fieldopsapp.nl) zelf ook structured data hebben; deze
sitemap exposeert alleen wat er op deze host live staat.

`/portaal` en `/reset-wachtwoord` zijn bewust uitgesloten — die zijn `noindex`
en horen niet in een sitemap (zou tegenstrijdige signalen aan crawlers geven).
"""

from __future__ import annotations
from datetime import datetime, timezone
import os

from fastapi import APIRouter, Request
from fastapi.responses import Response

router = APIRouter(tags=["SEO"])


# Production-host voor canonical/og links. Render's RENDER_EXTERNAL_URL is de
# service-URL (*.onrender.com) en NIET wat we als canonical willen — die staat
# achter het custom domain. We respecteren alleen een expliciete PUBLIC_HOST
# en vallen anders terug op de bekende productie-host.
DEFAULT_PUBLIC_HOST = "https://portaal.fieldopsapp.nl"


def public_host() -> str:
    """Geef de canonical host terug. Override via PUBLIC_HOST env (bv. voor
    staging of een ander custom domein); zonder override gebruiken we het
    productie-domain. RENDER_EXTERNAL_URL wordt bewust GENEGEERD om te
    voorkomen dat de sitemap *.onrender.com URLs genereert."""
    host = os.getenv("PUBLIC_HOST") or DEFAULT_PUBLIC_HOST
    return host.rstrip("/")


# Statische lijst met indexeerbare publieke routes. /portaal en
# /reset-wachtwoord staan hier bewust NIET — die zijn noindex.
# (loc_path, changefreq, priority)
PUBLIC_ROUTES: list[tuple[str, str, str]] = [
    ("/",            "weekly",  "0.9"),
    ("/developers",  "weekly",  "0.8"),
    ("/whitepaper",  "monthly", "0.7"),
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
    """XML-sitemap conform sitemaps.org schema. Bevat alleen routes die
    indexeerbaar zijn — gated/noindex pages horen niet in een sitemap."""
    host = public_host()
    today = datetime.now(timezone.utc).date().isoformat()

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]

    for path, changefreq, priority in PUBLIC_ROUTES:
        loc = f"{host}{path}"
        parts.append("  <url>")
        parts.append(f"    <loc>{loc}</loc>")
        parts.append(f"    <lastmod>{today}</lastmod>")
        parts.append(f"    <changefreq>{changefreq}</changefreq>")
        parts.append(f"    <priority>{priority}</priority>")
        parts.append("  </url>")

    parts.append("</urlset>")
    body = "\n".join(parts) + "\n"

    return Response(
        content=body,
        media_type="application/xml; charset=utf-8",
        headers={"Cache-Control": "public, max-age=3600"},
    )
