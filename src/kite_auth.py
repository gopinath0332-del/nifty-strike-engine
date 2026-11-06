"""
src/kite_auth.py
================
Helpers for generating and refreshing the Zerodha Kite Connect access token.

The access token expires at midnight every day. Run ``scripts/refresh_token.py``
each morning before market open (or automate via cron on the Pi).

Flow
----
  1. Generate login URL → open in browser
  2. After login, Kite redirects to your redirect URL with ?request_token=…
  3. Paste the full redirect URL (or just the request_token) here
  4. Exchange for access_token → saved back to .env
"""

from __future__ import annotations

import logging
import os
import re

from kiteconnect import KiteConnect

logger = logging.getLogger(__name__)


def get_login_url(api_key: str) -> str:
    """Return the Kite login URL for the given API key."""
    kite = KiteConnect(api_key=api_key)
    return kite.login_url()


def generate_access_token(api_key: str, api_secret: str, request_token: str) -> str:
    """
    Exchange a request_token for an access_token.

    Parameters
    ----------
    api_key       : Kite Connect API key
    api_secret    : Kite Connect API secret
    request_token : Token from the redirect URL after user login

    Returns
    -------
    str
        The new access token (valid until midnight).
    """
    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)
    access_token: str = session["access_token"]
    logger.info("Access token generated successfully.")
    return access_token


def save_access_token_to_env(env_path: str, access_token: str) -> None:
    """
    Update KITE_ACCESS_TOKEN in an existing .env file in-place.
    If the key is not present, appends it.

    Parameters
    ----------
    env_path     : Absolute path to the .env file
    access_token : New access token string
    """
    if not os.path.exists(env_path):
        raise FileNotFoundError(f".env file not found at: {env_path}")

    with open(env_path, "r", encoding="utf-8") as fh:
        content = fh.read()

    pattern = r"^(KITE_ACCESS_TOKEN\s*=\s*).*$"
    replacement = f"KITE_ACCESS_TOKEN={access_token}"

    if re.search(pattern, content, flags=re.MULTILINE):
        new_content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    else:
        new_content = content.rstrip("\n") + f"\n{replacement}\n"

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.write(new_content)

    logger.info("KITE_ACCESS_TOKEN updated in %s", env_path)


def build_authenticated_kite(api_key: str, access_token: str) -> KiteConnect:
    """Return an authenticated KiteConnect instance ready for API calls."""
    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)
    return kite

