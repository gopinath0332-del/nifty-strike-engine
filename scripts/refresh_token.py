"""
scripts/refresh_token.py
========================
Interactive CLI to refresh the Zerodha Kite Connect access token.

Run this script every morning before market open (09:15 IST).

Steps
-----
  1. Prints the Kite login URL.
  2. You open it in a browser and log in.
  3. After login, Kite redirects to your redirect URL — copy the full URL
     or just the `request_token` query parameter from the address bar.
  4. Paste it here → the script updates KITE_ACCESS_TOKEN in .env.

Usage
-----
    python scripts/refresh_token.py
    # or with an explicit .env path:
    python scripts/refresh_token.py --env /path/to/.env
"""

from __future__ import annotations

import argparse
import os
import sys
import urllib.parse

# Allow running from project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.kite_auth import (
    build_authenticated_kite,
    generate_access_token,
    get_login_url,
    save_access_token_to_env,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refresh Zerodha Kite access token")
    parser.add_argument(
        "--env",
        default=os.path.join(os.path.dirname(__file__), "..", ".env"),
        help="Path to .env file (default: project root .env)",
    )
    return parser.parse_args()


def extract_request_token(raw: str) -> str:
    """Extract request_token from a full redirect URL or return raw string."""
    raw = raw.strip()
    if raw.startswith("http"):
        parsed = urllib.parse.urlparse(raw)
        params = urllib.parse.parse_qs(parsed.query)
        tokens = params.get("request_token", [])
        if not tokens:
            raise ValueError("No 'request_token' found in the URL.")
        return tokens[0]
    # Assume user pasted the token directly
    return raw


def main() -> None:
    args   = parse_args()
    env_path = os.path.abspath(args.env)

    if not os.path.exists(env_path):
        print(f"[ERROR] .env file not found at: {env_path}")
        print("  Create it from .env.example first.")
        sys.exit(1)

    # Load credentials from .env
    from dotenv import dotenv_values
    cfg = dotenv_values(env_path)
    api_key    = cfg.get("KITE_API_KEY", "").strip()
    api_secret = cfg.get("KITE_API_SECRET", "").strip()

    if not api_key or not api_secret:
        print("[ERROR] KITE_API_KEY or KITE_API_SECRET missing in .env")
        sys.exit(1)

    login_url = get_login_url(api_key)

    print("\n" + "=" * 60)
    print("  Nifty Strike Engine — Token Refresh")
    print("=" * 60)
    print(f"\n1. Open the following URL in your browser:\n\n   {login_url}\n")
    print("2. Log in with your Zerodha credentials.")
    print("3. After login, copy the full redirect URL from your browser")
    print("   (or just the 'request_token' value from the URL).\n")

    raw_input = input("Paste the redirect URL or request_token here:\n> ").strip()

    try:
        request_token = extract_request_token(raw_input)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    print(f"\nExtracting access token for request_token={request_token[:8]}…")

    try:
        access_token = generate_access_token(api_key, api_secret, request_token)
    except Exception as exc:
        print(f"[ERROR] Failed to generate access token: {exc}")
        sys.exit(1)

    save_access_token_to_env(env_path, access_token)
    print(f"\n✅ Access token updated in {env_path}")

    # Quick validation
    try:
        kite    = build_authenticated_kite(api_key, access_token)
        profile = kite.profile()
        print(f"✅ Logged in as: {profile.get('user_name')} ({profile.get('user_id')})")
    except Exception as exc:
        print(f"[WARNING] Token saved but validation call failed: {exc}")


if __name__ == "__main__":
    main()

