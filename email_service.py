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
    role_labels = {
        "admin": "Beheerder",
        "manager": "Projectleider",
        "contractor": "Aannemer",
        "inspector": "Toezichthouder",
        "technician": "Technicus",
        "viewer": "Opdrachtgever",
    }
    role_label = role_labels.get(role, role)
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
        f"Welkom bij FieldOps - uw account is klaar",
        _base_template(content, "Account geactiveerd"),
    )
