"""
FieldOps Email Service
Verstuurt uitnodigingen, wachtwoord resets en notificaties via Resend API.
"""
import os
import json
import urllib.request

# Resend API configuratie
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "FieldOps <onboarding@resend.dev>")
FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.fieldopsapp.nl")
PORTAAL_URL = os.getenv("PORTAAL_URL", os.getenv("RENDER_EXTERNAL_URL", "https://portaal.fieldopsapp.nl"))

# Backwards compatibility
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")


def _base_template(content: str, title: str = "FieldOps") -> str:
    """Basis HTML email template in FieldOps stijl."""
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:#f4f6f8;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f8;padding:40px 20px;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0" style="background:#ffffff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
<!-- Header -->
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);padding:28px 40px;">
<table width="100%"><tr>
<td><span style="color:#ffffff;font-size:22px;font-weight:700;letter-spacing:-0.5px;">FieldOps</span>
<span style="color:rgba(255,255,255,0.7);font-size:12px;margin-left:8px;">Wegen &amp; Waterbouw</span></td>
</tr></table>
</td></tr>
<!-- Content -->
<tr><td style="padding:36px 40px 28px;">
{content}
</td></tr>
<!-- Footer -->
<tr><td style="padding:20px 40px 28px;border-top:1px solid #e8ecf0;">
<p style="color:#94a3b8;font-size:12px;margin:0;line-height:1.6;">
Dit is een automatisch bericht van FieldOps.<br>
<a href="{FRONTEND_URL}" style="color:#0284c7;text-decoration:none;">www.fieldopsapp.nl</a>
</p>
</td></tr>
</table>
</td></tr></table>
</body></html>"""


# Laatste error voor debugging via health endpoint
_LAST_EMAIL_ERROR = {"to": None, "subject": None, "error": None, "body": None}


def get_last_email_error():
    """Retourneer de laatste email error (voor debug doeleinden)."""
    return dict(_LAST_EMAIL_ERROR)


def send_email(to_email: str, subject: str, html_content: str) -> bool:
    """Verstuur een email via Resend API. Returns True bij succes."""
    global _LAST_EMAIL_ERROR
    if not RESEND_API_KEY:
        print(f"[EMAIL] Resend API key niet geconfigureerd - email naar {to_email} niet verstuurd")
        _LAST_EMAIL_ERROR = {"to": to_email, "subject": subject, "error": "RESEND_API_KEY not set", "body": None}
        return False

    data = json.dumps({
        "from": FROM_EMAIL,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=data,
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
            # User-Agent nodig om Cloudflare error 1010 te voorkomen
            # (Cloudflare blokkeert default urllib/python User-Agent)
            "User-Agent": "FieldOps-Backend/1.0 (+https://www.fieldopsapp.nl)",
            "Accept": "application/json",
        },
    )
    try:
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode())
        print(f"[EMAIL] Verstuurd naar {to_email}: {subject} (id={result.get('id', '?')})")
        _LAST_EMAIL_ERROR = {"to": to_email, "subject": subject, "error": None, "body": None}
        return True
    except urllib.error.HTTPError as e:
        # Lees de response body voor details over wat er mis ging
        try:
            error_body = e.read().decode()
        except Exception:
            error_body = "(could not read body)"
        print(f"[EMAIL] HTTP {e.code} bij {to_email}: {error_body}")
        _LAST_EMAIL_ERROR = {
            "to": to_email,
            "subject": subject,
            "error": f"HTTP {e.code} {e.reason}",
            "body": error_body,
        }
        return False
    except Exception as e:
        print(f"[EMAIL] Onverwachte fout bij verzenden naar {to_email}: {type(e).__name__}: {e}")
        _LAST_EMAIL_ERROR = {
            "to": to_email,
            "subject": subject,
            "error": f"{type(e).__name__}: {e}",
            "body": None,
        }
        return False


def send_invitation_email(
    to_email: str,
    inviter_name: str,
    org_name: str,
    role: str,
    token: str,
) -> bool:
    """Verstuur uitnodiging email met accepteer link."""
    from permissions import label_for
    role_label = label_for(role)
    accept_url = f"{PORTAAL_URL}/uitnodiging?token={token}"

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Je bent uitgenodigd!</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">
<strong>{inviter_name}</strong> heeft je uitgenodigd om lid te worden van <strong>{org_name}</strong> op FieldOps.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;margin-bottom:24px;">
<tr><td style="padding:20px 24px;">
<table width="100%">
<tr>
<td style="padding:4px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">ORGANISATIE</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{org_name}</span></td>
<td style="padding:4px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">ROL</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{role_label}</span></td>
</tr>
</table>
</td></tr></table>

<table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);border-radius:12px;padding:14px 36px;">
<a href="{accept_url}" style="color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;display:inline-block;">
Uitnodiging accepteren</a>
</td></tr></table>

<p style="color:#94a3b8;font-size:13px;margin:0;">
Of kopieer deze link: <a href="{accept_url}" style="color:#0284c7;">{accept_url}</a><br>
Deze uitnodiging is 7 dagen geldig.
</p>"""

    return send_email(to_email, f"Uitnodiging voor {org_name} op FieldOps", _base_template(content, "Uitnodiging"))


def send_password_reset_email(to_email: str, token: str, user_name: str = "") -> bool:
    """Verstuur wachtwoord reset email."""
    reset_url = f"{PORTAAL_URL}/reset-wachtwoord?token={token}"
    greeting = f"Hoi {user_name}," if user_name else "Hoi,"

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Wachtwoord resetten</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">
{greeting}<br>
Je hebt een verzoek ingediend om je wachtwoord te resetten. Klik op de knop hieronder om een nieuw wachtwoord in te stellen.
</p>

<table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);border-radius:12px;padding:14px 36px;">
<a href="{reset_url}" style="color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;display:inline-block;">
Nieuw wachtwoord instellen</a>
</td></tr></table>

<p style="color:#94a3b8;font-size:13px;margin:0;">
Of kopieer deze link: <a href="{reset_url}" style="color:#0284c7;">{reset_url}</a><br>
Deze link is 1 uur geldig. Heb je dit niet aangevraagd? Dan kun je deze email negeren.
</p>"""

    return send_email(to_email, "Wachtwoord resetten - FieldOps", _base_template(content, "Wachtwoord reset"))


def send_welcome_email(to_email: str, user_name: str, org_name: str) -> bool:
    """Verstuur welkom email na account aanmaken."""
    login_url = f"{FRONTEND_URL}"

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Welkom bij FieldOps!</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">
Hoi {user_name},<br>
Je account bij <strong>{org_name}</strong> is aangemaakt. Je kunt nu inloggen en direct beginnen met veldregistraties.
</p>

<table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);border-radius:12px;padding:14px 36px;">
<a href="{login_url}" style="color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;display:inline-block;">
Inloggen op FieldOps</a>
</td></tr></table>

<p style="color:#64748b;font-size:13px;line-height:1.6;margin:0;">
<strong>Wat kun je doen?</strong><br>
&bull; Schades en inspecties registreren met GPS en foto's<br>
&bull; Projecten beheren en meldingen volgen<br>
&bull; Rapportages exporteren als PDF of CSV
</p>"""

    return send_email(to_email, f"Welkom bij FieldOps - {org_name}", _base_template(content, "Welkom"))


# --- Demo aanvraag emails ---

ADMIN_NOTIFICATION_EMAIL = os.getenv("ADMIN_NOTIFICATION_EMAIL", "info@fieldopsapp.nl")


def send_demo_admin_notification(demo) -> bool:
    """Notificatie naar admin (info@fieldopsapp.nl) bij nieuwe demo aanvraag."""
    plan_label = (demo.plan.value if demo.plan else "starter").title()
    phone_display = demo.phone or "(niet opgegeven)"

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Nieuwe demo aanvraag</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">
Er is een nieuwe demo aanvraag binnengekomen via <strong>fieldopsapp.nl</strong>. Log in op het portaal om goed te keuren.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;margin-bottom:24px;">
<tr><td style="padding:20px 24px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">NAAM</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{demo.first_name} {demo.last_name}</span></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">BEDRIJF</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{demo.company_name}</span></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">E-MAIL</span><br>
<a href="mailto:{demo.email}" style="color:#0284c7;font-size:14px;font-weight:500;">{demo.email}</a></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">TELEFOON</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{phone_display}</span></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">GEWENST PLAN</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{plan_label}</span></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">AANTAL GEBRUIKERS</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{demo.num_users}</span></td></tr>
</table>
</td></tr></table>

<table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);border-radius:12px;padding:14px 36px;">
<a href="{PORTAAL_URL}/portaal" style="color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;display:inline-block;">
Openen in portaal</a>
</td></tr></table>

<p style="color:#94a3b8;font-size:13px;margin:0;">
Neem contact op met de aanvrager voor een kennismaking of keur direct goed via het admin overzicht.
</p>"""

    return send_email(
        ADMIN_NOTIFICATION_EMAIL,
        f"Nieuwe demo aanvraag: {demo.company_name}",
        _base_template(content, "Nieuwe demo aanvraag"),
    )


def send_demo_confirmation(demo) -> bool:
    """Bevestigingsmail naar aanvrager na indienen demo aanvraag."""
    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Bedankt voor uw aanvraag, {demo.first_name}!</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">
We hebben uw demo aanvraag voor <strong>{demo.company_name}</strong> ontvangen. Iemand van het FieldOps team neemt binnen <strong>1 werkdag</strong> persoonlijk contact met u op voor een kennismaking.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;margin-bottom:24px;">
<tr><td style="padding:20px 24px;">
<p style="color:#1e293b;font-size:14px;font-weight:600;margin:0 0 12px;">Wat gebeurt er nu?</p>
<p style="color:#64748b;font-size:13px;line-height:1.8;margin:0;">
1. &nbsp;We bespreken kort uw situatie en wensen<br>
2. &nbsp;Uw demo account wordt geactiveerd<br>
3. &nbsp;U ontvangt een welkomstmail met inloggegevens<br>
4. &nbsp;U kunt FieldOps 7 dagen gratis uitproberen
</p>
</td></tr></table>

<p style="color:#64748b;font-size:13px;line-height:1.6;margin:0;">
Heeft u in de tussentijd vragen? Stuur een mail naar <a href="mailto:info@fieldopsapp.nl" style="color:#0284c7;">info@fieldopsapp.nl</a>.
</p>

<p style="color:#94a3b8;font-size:13px;margin:20px 0 0;">
Met vriendelijke groet,<br>
<strong style="color:#1e293b;">Faris Osman</strong><br>
FieldOps
</p>"""

    return send_email(
        demo.email,
        "Uw demo aanvraag is ontvangen - FieldOps",
        _base_template(content, "Demo aanvraag ontvangen"),
    )


def send_demo_welcome(user, password: str, org) -> bool:
    """Welkomstmail na goedkeuring demo: inloggegevens + contact tekst."""
    login_url = FRONTEND_URL
    user_name = f"{user.first_name} {user.last_name}".strip() or "daar"

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Welkom bij FieldOps, {user.first_name}!</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">
Uw demo aanvraag voor <strong>{org.name}</strong> is goedgekeurd. Hieronder vindt u uw inloggegevens. Iemand van FieldOps neemt binnen 1 werkdag persoonlijk contact met u op voor een korte kennismaking en uitleg.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;margin-bottom:24px;">
<tr><td style="padding:20px 24px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">APP</span><br>
<a href="{login_url}" style="color:#0284c7;font-size:14px;font-weight:500;">app.fieldopsapp.nl</a></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">E-MAILADRES</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{user.email}</span></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">WACHTWOORD</span><br>
<span style="color:#0284c7;font-size:14px;font-weight:600;font-family:monospace;">{password}</span></td></tr>
</table>
</td></tr></table>

<table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);border-radius:12px;padding:14px 36px;">
<a href="{login_url}" style="color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;display:inline-block;">
Inloggen op FieldOps</a>
</td></tr></table>

<p style="color:#64748b;font-size:13px;line-height:1.6;margin:0 0 20px;">
<strong>Tip:</strong> Wijzig uw wachtwoord na het eerste inloggen via Instellingen.
</p>

<p style="color:#94a3b8;font-size:13px;margin:0;">
Met vriendelijke groet,<br>
<strong style="color:#1e293b;">Faris Osman</strong><br>
FieldOps
</p>"""

    return send_email(
        user.email,
        "Welkom bij FieldOps - uw account is klaar",
        _base_template(content, "Account geactiveerd"),
    )


def send_license_welcome(user, password: str, org, *, seats: int = 1) -> bool:
    """Welkomstmail na een betaalde licentie (Shopify-bestelling).

    Bewust apart van send_demo_welcome: die tekst spreekt over een goedgekeurde
    demo-aanvraag, wat voor een betalende klant niet klopt.
    """
    login_url = FRONTEND_URL
    seats_tekst = "1 gebruiker" if seats == 1 else f"{seats} gebruikers"

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">Welkom bij FieldOps, {user.first_name}!</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 24px;">
Je bestelling is gelukt en de omgeving van <strong>{org.name}</strong> staat klaar voor {seats_tekst}. Hieronder vind je je inloggegevens.
</p>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;margin-bottom:24px;">
<tr><td style="padding:20px 24px;">
<table width="100%" cellpadding="0" cellspacing="0">
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">APP</span><br>
<a href="{login_url}" style="color:#0284c7;font-size:14px;font-weight:500;">{login_url}</a></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">E-MAILADRES</span><br>
<span style="color:#1e293b;font-size:14px;font-weight:500;">{user.email}</span></td></tr>
<tr><td style="padding:6px 0;"><span style="color:#94a3b8;font-size:12px;font-weight:600;">TIJDELIJK WACHTWOORD</span><br>
<span style="color:#0284c7;font-size:14px;font-weight:600;font-family:monospace;">{password}</span></td></tr>
</table>
</td></tr></table>

<table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);border-radius:12px;padding:14px 36px;">
<a href="{login_url}" style="color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;display:inline-block;">
Inloggen op FieldOps</a>
</td></tr></table>

<p style="color:#64748b;font-size:13px;line-height:1.6;margin:0 0 20px;">
Bij je eerste keer inloggen vragen we je meteen een eigen wachtwoord te kiezen.
</p>

<p style="color:#94a3b8;font-size:13px;margin:0;">
Met vriendelijke groet,<br>
<strong style="color:#1e293b;">Faris Osman</strong><br>
FieldOps
</p>"""

    return send_email(
        user.email,
        "Welkom bij FieldOps - je account is klaar",
        _base_template(content, "Account geactiveerd"),
    )


def send_oplevering_email(oplevering, recipients: list[str], *, trigger: str = "manual") -> dict:
    """Verstuur oplever-formulier per email naar opdrachtgever + aannemer.

    Args:
        oplevering: Oplevering SQLAlchemy object (relationship .punten geladen).
        recipients: lijst email-adressen om naar te versturen.
        trigger: "manual" (handmatige knop) of "auto_status_change" (status
            ging over naar opgeleverd). Verschijnt in subject.

    Returns:
        dict met counts: {"sent": int, "failed": int, "skipped": int}
    """
    import json

    if not recipients:
        return {"sent": 0, "failed": 0, "skipped": 0}

    # Header-info
    status_labels = {"concept": "Concept", "opgeleverd": "Opgeleverd",
                     "aanvaard": "Aanvaard", "afgewezen": "Afgewezen"}
    status_colors = {"concept": "#64748b", "opgeleverd": "#0284c7",
                     "aanvaard": "#16a34a", "afgewezen": "#dc2626"}
    status_label = status_labels.get(oplevering.status, oplevering.status or "—")
    status_color = status_colors.get(oplevering.status, "#64748b")

    datum_str = (
        oplevering.datum_oplevering.strftime("%d-%m-%Y")
        if oplevering.datum_oplevering else "—"
    )

    # Extra-vragen ontleden
    eq = {}
    if oplevering.extra_questions_json:
        try:
            eq = json.loads(oplevering.extra_questions_json) or {}
        except (json.JSONDecodeError, TypeError):
            eq = {}
    extra_rows = ""
    eq_labels = {"weer": "Weersomstandigheden", "veiligheid": "Veiligheid",
                 "normen": "Bouwbesluit / normen", "netheid": "Netheid / afval"}
    for k, label in eq_labels.items():
        v = eq.get(k)
        if v:
            extra_rows += (
                f'<tr><td style="padding:4px 0;color:#64748b;font-size:13px;">{label}</td>'
                f'<td style="padding:4px 0;color:#1e293b;font-size:13px;font-weight:500;">{v}</td></tr>'
            )

    # Restpunten-rijen
    punten_html = ""
    for p in (oplevering.punten or []):
        punt_status_colors = {"gereed": "#16a34a", "restpunt": "#f59e0b", "afgekeurd": "#dc2626"}
        ps_color = punt_status_colors.get(p.status, "#64748b")
        photos = []
        if p.photo_url:
            photos.append(
                f'<a href="{p.photo_url}" style="display:inline-block;margin-right:6px;">'
                f'<img src="{p.photo_url}" style="width:80px;height:80px;object-fit:cover;border:2px solid #f59e0b;border-radius:6px;" alt="vóór"></a>'
            )
        if p.photo_url_after:
            photos.append(
                f'<a href="{p.photo_url_after}" style="display:inline-block;">'
                f'<img src="{p.photo_url_after}" style="width:80px;height:80px;object-fit:cover;border:2px solid #16a34a;border-radius:6px;" alt="na"></a>'
            )
        photos_html = ("".join(photos)) or '<span style="color:#94a3b8;font-size:12px;">geen foto\'s</span>'
        uitvoering_html = (
            f'<div style="color:#64748b;font-size:12px;margin-top:6px;">'
            f'<strong>Methode:</strong> {p.uitvoeringsmethode}</div>'
            if p.uitvoeringsmethode else ""
        )
        punten_html += f"""
<tr><td style="padding:12px 0;border-bottom:1px solid #e2e8f0;">
  <div style="display:flex;align-items:flex-start;gap:12px;">
    <div style="flex:1;">
      <div style="font-family:monospace;font-size:11px;background:#f1f5f9;color:#1e293b;padding:2px 8px;border-radius:4px;display:inline-block;font-weight:700;">{p.code}</div>
      <span style="background:{ps_color}20;color:{ps_color};font-size:10px;padding:2px 8px;border-radius:100px;font-weight:700;letter-spacing:.05em;margin-left:6px;">{p.status.upper()}</span>
      <div style="color:#1e293b;font-size:14px;margin-top:6px;font-weight:500;">{p.omschrijving}</div>
      {uitvoering_html}
    </div>
    <div style="text-align:right;">{photos_html}</div>
  </div>
</td></tr>"""

    if not punten_html:
        punten_html = '<tr><td style="padding:20px;text-align:center;color:#94a3b8;font-size:13px;">Geen restpunten toegevoegd.</td></tr>'

    notes_html = ""
    if oplevering.notes:
        notes_html = f"""
<table width="100%" cellpadding="0" cellspacing="0" style="background:#fef3c7;border-left:3px solid #f59e0b;border-radius:6px;margin:16px 0;">
<tr><td style="padding:12px 16px;">
<div style="color:#78350f;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px;">Opmerkingen / restpunten</div>
<div style="color:#1e293b;font-size:13px;line-height:1.5;">{oplevering.notes}</div>
</td></tr></table>"""

    trigger_note = (
        "Deze oplevering is zojuist gemarkeerd als <strong>Opgeleverd</strong>."
        if trigger == "auto_status_change" else
        "Hierbij het oplever-overzicht zoals opgevraagd."
    )

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 4px;">Oplever-formulier</h2>
<p style="color:#64748b;font-size:14px;margin:0 0 16px;">{trigger_note}</p>

<table width="100%" cellpadding="0" cellspacing="0" style="background:#f8fafc;border-radius:12px;margin-bottom:20px;">
<tr><td style="padding:18px 22px;">
<div style="font-size:18px;font-weight:700;color:#1e293b;margin-bottom:4px;">{oplevering.title}</div>
<span style="background:{status_color}20;color:{status_color};font-size:11px;padding:3px 10px;border-radius:100px;font-weight:700;letter-spacing:.05em;">{status_label.upper()}</span>
<table width="100%" style="margin-top:14px;">
<tr><td style="padding:4px 0;color:#64748b;font-size:13px;width:160px;">Project</td><td style="padding:4px 0;color:#1e293b;font-size:13px;font-weight:500;">{(oplevering.project.name if oplevering.project else '—')}</td></tr>
<tr><td style="padding:4px 0;color:#64748b;font-size:13px;">Locatie</td><td style="padding:4px 0;color:#1e293b;font-size:13px;font-weight:500;">{oplevering.locatie or '—'}</td></tr>
<tr><td style="padding:4px 0;color:#64748b;font-size:13px;">Datum oplevering</td><td style="padding:4px 0;color:#1e293b;font-size:13px;font-weight:500;">{datum_str}</td></tr>
<tr><td style="padding:4px 0;color:#64748b;font-size:13px;">Opdrachtgever</td><td style="padding:4px 0;color:#1e293b;font-size:13px;font-weight:500;">{oplevering.opdrachtgever_naam or '—'}{(' · ' + oplevering.opdrachtgever_email) if oplevering.opdrachtgever_email else ''}</td></tr>
<tr><td style="padding:4px 0;color:#64748b;font-size:13px;">Aannemer</td><td style="padding:4px 0;color:#1e293b;font-size:13px;font-weight:500;">{oplevering.aannemer_naam or '—'}{(' · ' + oplevering.aannemer_email) if oplevering.aannemer_email else ''}</td></tr>
{extra_rows}
</table>
</td></tr></table>

{notes_html}

<h3 style="color:#1e293b;font-size:16px;margin:16px 0 8px;">Restpunten ({len(oplevering.punten or [])})</h3>
<table width="100%" cellpadding="0" cellspacing="0" style="border-top:1px solid #e2e8f0;">
{punten_html}
</table>

<p style="color:#94a3b8;font-size:12px;margin:24px 0 0;">
Reageren? Antwoord direct op deze mail.<br>
Deze e-mail is automatisch verstuurd via FieldOps.
</p>"""

    sent, failed = 0, 0
    subject = f"[FieldOps] Oplevering {status_label.lower()}: {oplevering.title}"
    for email in recipients:
        ok = send_email(email, subject, _base_template(content, "Oplever-formulier"))
        if ok:
            sent += 1
        else:
            failed += 1
    return {"sent": sent, "failed": failed, "skipped": 0}


def send_assignment_notification(
    to_email: str,
    *,
    recipient_name: str,
    assigner_name: str,
    melding_title: str,
    melding_category: str = "",
    melding_priority: str = "normaal",
    melding_id: str = "",
) -> bool:
    """Email-notificatie bij toewijzing aan melding.

    Wordt aangeroepen vanuit notifier.notify() als notif_type='assigned'.
    Resend stuurt; bij geen API-key faalt 'ie stilletjes (best-effort).
    """
    greeting = f"Hoi {recipient_name}," if recipient_name else "Hoi,"
    melding_url = f"{PORTAAL_URL}/portaal#meldingen" + (f"?id={melding_id}" if melding_id else "")
    prio_colors = {
        "kritiek": "#dc2626",
        "hoog": "#ea580c",
        "normaal": "#0284c7",
        "laag": "#64748b",
    }
    prio_color = prio_colors.get(melding_priority, "#0284c7")

    content = f"""
<h2 style="color:#1e293b;font-size:22px;margin:0 0 8px;">🎯 Toegewezen aan een melding</h2>
<p style="color:#64748b;font-size:15px;line-height:1.6;margin:0 0 20px;">
{greeting}<br>
<strong>{assigner_name or "Een collega"}</strong> heeft je toegewezen aan een melding in FieldOps.
</p>

<table cellpadding="0" cellspacing="0" style="width:100%;background:#f8fafc;border-radius:12px;padding:0;margin:0 0 24px;border-left:4px solid {prio_color};">
<tr><td style="padding:18px 20px;">
<p style="margin:0 0 6px;color:#1e293b;font-size:16px;font-weight:600;">{melding_title}</p>
<p style="margin:0;color:#64748b;font-size:13px;line-height:1.5;">
{"Categorie: " + melding_category + " · " if melding_category else ""}
Prioriteit: <strong style="color:{prio_color};text-transform:capitalize;">{melding_priority}</strong>
</p>
</td></tr></table>

<table cellpadding="0" cellspacing="0" style="margin:0 auto 24px;">
<tr><td style="background:linear-gradient(135deg,#0284c7,#0369a1);border-radius:12px;padding:14px 36px;">
<a href="{melding_url}" style="color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;display:inline-block;">
Open melding in FieldOps</a>
</td></tr></table>

<p style="color:#94a3b8;font-size:13px;margin:0;">
Je ontvangt deze email omdat een collega je heeft toegewezen aan een melding. Wil je deze meldingen liever niet via email ontvangen? Pas dat aan in <a href="{PORTAAL_URL}/portaal#instellingen" style="color:#0284c7;">Instellingen</a>.
</p>"""

    return send_email(
        to_email,
        f"🎯 Toegewezen: {melding_title[:80]}",
        _base_template(content, "Toegewezen aan melding"),
    )
