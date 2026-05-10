"""SEO-endpoint tests:

  /robots.txt   — content-type, sitemap-link, disallow rules
  /sitemap.xml  — XML-vorm, hreflang per taal voor /portaal
  /developers   — structured data + Open Graph + canonical aanwezig
"""

import re


def test_robots_txt_content_type(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")


def test_robots_txt_disallows_api(client):
    r = client.get("/robots.txt")
    body = r.text
    assert "User-agent: *" in body
    assert "Disallow: /api/" in body
    assert "Disallow: /openapi.json" in body
    assert "Disallow: /docs" in body


def test_robots_txt_links_sitemap(client):
    r = client.get("/robots.txt")
    assert "Sitemap:" in r.text
    assert "/sitemap.xml" in r.text


def test_sitemap_xml_content_type(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")


def test_sitemap_xml_well_formed(client):
    r = client.get("/sitemap.xml")
    body = r.text
    assert body.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<urlset" in body and "</urlset>" in body
    assert body.count("<url>") == body.count("</url>")
    # Minimaal 3 publieke routes
    assert body.count("<url>") >= 3


def test_sitemap_xml_includes_developers(client):
    r = client.get("/sitemap.xml")
    assert "/developers" in r.text
    assert "/whitepaper" in r.text


def test_sitemap_xml_excludes_noindex_routes(client):
    """noindex pages horen niet in sitemap — zou tegenstrijdige signalen
    aan crawlers geven (sitemap zegt 'index dit', meta zegt 'doe niet')."""
    import xml.etree.ElementTree as ET
    r = client.get("/sitemap.xml")
    root = ET.fromstring(r.text)
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [el.text for el in root.findall(".//sm:loc", ns)]
    # Geen enkele <loc> mag eindigen op /portaal of /reset-wachtwoord
    for path in ("/portaal", "/reset-wachtwoord"):
        assert not any(loc.endswith(path) for loc in locs), \
            f"{path} should not appear as a sitemap loc"


def test_sitemap_uses_canonical_host_not_render_url(monkeypatch, client):
    """RENDER_EXTERNAL_URL mag niet lekken in de sitemap — anders krijgen
    crawlers *.onrender.com URLs ipv het apex-domein."""
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://fieldops-api.onrender.com")
    monkeypatch.delenv("PUBLIC_HOST", raising=False)
    r = client.get("/sitemap.xml")
    assert "onrender.com" not in r.text
    assert "portaal.fieldopsapp.nl" in r.text


def test_sitemap_respects_public_host_override(monkeypatch, client):
    """Expliciete PUBLIC_HOST wint voor staging/alternate domains."""
    monkeypatch.setenv("PUBLIC_HOST", "https://staging.fieldopsapp.nl")
    r = client.get("/sitemap.xml")
    assert "staging.fieldopsapp.nl" in r.text


def test_developers_page_has_canonical(client):
    r = client.get("/developers")
    assert r.status_code == 200
    assert '<link rel="canonical"' in r.text
    assert "portaal.fieldopsapp.nl/developers" in r.text


def test_developers_page_has_open_graph(client):
    r = client.get("/developers")
    body = r.text
    for prop in ("og:type", "og:url", "og:title", "og:description", "og:image"):
        assert f'property="{prop}"' in body, f"Missing OG: {prop}"


def test_developers_page_has_twitter_card(client):
    r = client.get("/developers")
    body = r.text
    assert 'name="twitter:card"' in body
    assert 'content="summary_large_image"' in body


def test_developers_page_has_jsonld_organization_and_software(client):
    """JSON-LD blok moet @graph hebben met Organization + SoftwareApplication."""
    r = client.get("/developers")
    body = r.text
    # Vind het script-blok
    m = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                  body, re.DOTALL)
    assert m, "JSON-LD script not found"
    import json
    data = json.loads(m.group(1))
    types = [item["@type"] for item in data.get("@graph", [])]
    assert "Organization" in types
    assert "SoftwareApplication" in types
    assert "BreadcrumbList" in types


def test_developers_page_robots_indexable(client):
    r = client.get("/developers")
    assert 'name="robots"' in r.text
    # Mag NIET noindex zijn — dit is de publieke landing
    assert "noindex" not in r.text.lower().split("name=\"robots\"")[1].split(">")[0]


def test_portaal_marked_noindex(client):
    """Admin-portal hoort niet in zoekresultaten — meta robots noindex."""
    r = client.get("/portaal")
    assert r.status_code == 200
    # Zoek het robots-blok specifiek — andere keywords kunnen ook 'noindex' bevatten
    assert re.search(r'<meta\s+name="robots"\s+content="noindex', r.text), \
        "portaal.html should have noindex meta"


def test_reset_password_marked_noindex(client):
    r = client.get("/reset-wachtwoord")
    assert r.status_code == 200
    assert re.search(r'<meta\s+name="robots"\s+content="noindex', r.text)
