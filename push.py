"""Web Push helper — VAPID-gesigneerde notifications naar PWA-clients.

Werkt met de standaard Web Push protocol (RFC 8030):
- Server tekent een JWT met VAPID private key + audience (origin van endpoint)
- POST naar de browser-vendor (FCM voor Chrome/Edge, Mozilla voor Firefox, APNs/web-push voor Safari)
- Browser-vendor deliver naar de service worker, die de push event afhandelt

Public-key (raw URL-safe base64 EC P-256) deelt de server met de browser bij
subscribe. Private-key blijft server-side.

Setup eenmalig:
1. Genereer keys: `python -c "from push import generate_vapid_keys; generate_vapid_keys()"`
2. Voeg toe aan Render env: VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT
3. Frontend pakt VAPID_PUBLIC_KEY op via /api/push/public-key endpoint

Bij ontbrekende keys: helper logt en doet niets (graceful no-op).
"""

from __future__ import annotations
import base64
import json
import os
from typing import Optional

# pywebpush is een lichte dependency die de hele encryptie/JWT/fetch afhandelt.
# Bij ontbreken: graceful disable.
try:
    from pywebpush import webpush, WebPushException  # type: ignore
    _PYWEBPUSH_AVAILABLE = True
except ImportError:
    _PYWEBPUSH_AVAILABLE = False
    WebPushException = Exception  # type: ignore


def vapid_public_key() -> Optional[str]:
    return os.environ.get("VAPID_PUBLIC_KEY") or None


def vapid_private_key() -> Optional[str]:
    return os.environ.get("VAPID_PRIVATE_KEY") or None


def vapid_subject() -> str:
    return os.environ.get("VAPID_SUBJECT") or "mailto:info@fieldopsapp.nl"


def is_configured() -> bool:
    return _PYWEBPUSH_AVAILABLE and bool(vapid_public_key() and vapid_private_key())


def send_push(subscription: dict, title: str, body: str,
              url: str = "/portaal", tag: Optional[str] = None,
              extra: Optional[dict] = None) -> tuple[bool, Optional[int], Optional[str]]:
    """Stuur één push. Return (succes, http_status, error_str).

    `subscription` is de dict {endpoint, keys: {p256dh, auth}} zoals door de
    browser uitgegeven. Extra data komt in de payload — service worker leest 't.
    """
    if not is_configured():
        return False, None, "vapid-not-configured"

    payload = {"title": title, "body": body, "url": url}
    if tag: payload["tag"] = tag
    if extra: payload["extra"] = extra

    try:
        resp = webpush(
            subscription_info=subscription,
            data=json.dumps(payload, default=str),
            vapid_private_key=vapid_private_key(),
            vapid_claims={"sub": vapid_subject()},
            ttl=86400,  # 24 uur — verloopt als device offline blijft
        )
        return True, getattr(resp, "status_code", 200), None
    except WebPushException as e:
        # 410 Gone of 404 = subscription is dood, caller moet opruimen
        status = getattr(getattr(e, "response", None), "status_code", None)
        return False, status, f"WebPushException: {str(e)[:200]}"
    except Exception as e:
        return False, None, f"{type(e).__name__}: {str(e)[:200]}"


def generate_vapid_keys() -> dict:
    """Helper voor lokaal/CI om eenmalig VAPID-keys te genereren.

    Print het paar — voeg toe aan Render env. Doe dit één keer en bewaar.
    """
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import ec

    private_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
    raw_priv = private_key.private_numbers().private_value.to_bytes(32, "big")
    pub = private_key.public_key().public_numbers()
    raw_pub = b"\x04" + pub.x.to_bytes(32, "big") + pub.y.to_bytes(32, "big")

    def b64u(b): return base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    out = {
        "VAPID_PRIVATE_KEY": b64u(raw_priv),
        "VAPID_PUBLIC_KEY": b64u(raw_pub),
    }
    print("Add to Render env:")
    for k, v in out.items():
        print(f"  {k}={v}")
    print("  VAPID_SUBJECT=mailto:info@fieldopsapp.nl")
    return out
