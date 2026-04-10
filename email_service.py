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


def send_email(to_email: str, subject: str, html_content: str) -> bool:
    """Verstuur een email via Resend API. Returns True bij succes."""
    if not RESEND_API_KEY:
        print(f"[EMAIL] Resend API key niet geconfigureerd - email naar {to_email} niet verstuurd")
        print(f"[EMAIL] Subject: {subject}")
        return False

    try:
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
        resp = urllib.request.urlopen(req)
        result = json.loads(resp.read().decode())
        print(f"[EMAIL] Verstuurd naar {to_email}: {subject} (id={result.get('id', '?')})")
        return True
    except Exception as e:
        print(f"[EMAIL] Fout bij verzenden naar {to_email}: {e}")
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
