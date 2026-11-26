"""Connexion Google (OAuth 2.0, client « Application Web ») pour l'API Google Ads.

Streamlit ouvre une nouvelle session au retour de Google : le paramètre `state`
est donc signé (HMAC) au lieu d'être gardé en mémoire.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import urllib.parse

import requests

from core.config import Settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/adwords"
STATE_TTL = 600  # secondes


def _sign(ts: str, secret: str) -> str:
    return hmac.new(secret.encode(), ts.encode(), hashlib.sha256).hexdigest()[:32]


def make_state(settings: Settings) -> str:
    ts = str(int(time.time()))
    return f"{ts}.{_sign(ts, settings.oauth_client_secret)}"


def check_state(state: str, settings: Settings) -> bool:
    try:
        ts, sig = state.split(".", 1)
        fresh = time.time() - int(ts) < STATE_TTL
    except ValueError:
        return False
    return fresh and hmac.compare_digest(sig, _sign(ts, settings.oauth_client_secret))


def login_url(settings: Settings) -> str:
    params = {
        "client_id": settings.oauth_client_id,
        "redirect_uri": settings.oauth_redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": make_state(settings),
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code(code: str, settings: Settings) -> str:
    """Échange le code d'autorisation contre un refresh token."""
    resp = requests.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": settings.oauth_client_id,
            "client_secret": settings.oauth_client_secret,
            "redirect_uri": settings.oauth_redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=20,
    )
    resp.raise_for_status()
    token = resp.json().get("refresh_token")
    if not token:
        raise RuntimeError("Google n'a pas renvoyé de refresh token.")
    return token
