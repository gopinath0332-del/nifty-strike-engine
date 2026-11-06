"""
scripts/auto_login.py
=====================
Zero-Touch Automated Morning Authentication for Zerodha Kite Connect using PyOTP.

Flow:
  1. Reads credentials (API Key, API Secret, User ID, Password, TOTP Secret) from .env.
  2. Generates current 6-digit TOTP code locally using PyOTP (RFC 6238).
  3. Authenticates against Zerodha login endpoints:
       POST https://kite.zerodha.com/api/login (User ID + Password)
       POST https://kite.zerodha.com/api/twofa (User ID + Request ID + TOTP)
  4. Follows Kite Connect authorization redirect to obtain `request_token`.
  5. Exchanges `request_token` with Kite API Secret to generate daily `access_token`.
  6. Automatically updates KITE_ACCESS_TOKEN in .env.
  7. Restarts systemd service 'nifty-strike-engine' (if running on Raspberry Pi / Linux).
  8. Sends success/failure notification to Discord webhook.

Usage:
  python scripts/auto_login.py
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import urllib.parse
from datetime import datetime

try:
    import zoneinfo
    IST = zoneinfo.ZoneInfo("Asia/Kolkata")
except Exception:
    import pytz
    IST = pytz.timezone("Asia/Kolkata")

import pyotp
import requests
from dotenv import dotenv_values

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.discord_notifier import DiscordNotifier
from src.kite_auth import generate_access_token, save_access_token_to_env

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [auto_login]: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("auto_login")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Zero-Touch Zerodha Auto-Login via TOTP")
    parser.add_argument(
        "--env",
        default=os.path.join(os.path.dirname(__file__), "..", ".env"),
        help="Path to .env file",
    )
    parser.add_argument(
        "--restart-service",
        action="store_true",
        help="Restart nifty-strike-engine systemd service upon success",
    )
    return parser.parse_args()


def perform_automated_login(
    api_key: str,
    api_secret: str,
    user_id: str,
    password: str,
    totp_secret: str,
) -> str:
    """
    Simulates Kite web auth flow using session requests + TOTP, returns new access_token.
    """
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    })

    # Step 0: Hit the login page first to establish base cookies (kf_session, __cf_bm, etc.)
    login_page_url = f"https://kite.trade/connect/login?v=3&api_key={api_key}"
    logger.info("Initializing session cookies...")
    init_resp = session.get(login_page_url, timeout=15)
    actual_login_url = init_resp.url

    # Step 1: Submit User ID & Password
    logger.info("Submitting login credentials for user: %s", user_id)
    login_url = "https://kite.zerodha.com/api/login"
    login_resp = session.post(
        login_url,
        data={"user_id": user_id, "password": password},
        timeout=15,
    )
    login_data = login_resp.json()

    if login_data.get("status") != "success":
        error_msg = login_data.get("message", "Login step failed")
        raise RuntimeError(f"Step 1 (User/Password) failed: {error_msg}")

    request_id = login_data["data"]["request_id"]
    logger.info("Credentials accepted. Request ID: %s", request_id)

    # Step 2: Generate TOTP code and submit Two-Factor Authentication
    totp = pyotp.TOTP(totp_secret.replace(" ", "").strip())
    twofa_pin = totp.now()
    logger.info("Generated TOTP code: %s", twofa_pin)

    twofa_url = "https://kite.zerodha.com/api/twofa"
    twofa_resp = session.post(
        twofa_url,
        data={
            "user_id": user_id,
            "request_id": request_id,
            "twofa_value": twofa_pin,
        },
        timeout=15,
    )
    twofa_data = twofa_resp.json()

    if twofa_data.get("status") != "success":
        error_msg = twofa_data.get("message", "2FA authentication failed")
        raise RuntimeError(f"Step 2 (TOTP 2FA) failed: {error_msg}")

    logger.info("TOTP verification successful.")

    # Step 3: Complete redirect flow with established session
    final_auth_url = f"{actual_login_url}&skip_session=true"
    auth_resp = session.get(final_auth_url, allow_redirects=True, timeout=15)

    final_url = auth_resp.url
    logger.debug("Final redirected URL: %s", final_url)

    parsed_url = urllib.parse.urlparse(final_url)
    query_params = urllib.parse.parse_qs(parsed_url.query)

    request_tokens = query_params.get("request_token", [])
    if not request_tokens:
        # Check if error or status returned in query
        status_param = query_params.get("status", [""])[0]
        msg_param = query_params.get("message", [""])[0]
        raise RuntimeError(
            f"Step 3 failed: request_token not found in redirect URL. "
            f"Status={status_param}, Msg={msg_param}"
        )

    request_token = request_tokens[0]
    logger.info("Request token acquired: %s...", request_token[:8])

    # Step 4: Exchange request_token for Kite access_token
    access_token = generate_access_token(api_key, api_secret, request_token)
    return access_token


def restart_systemd_service(service_name: str = "nifty-strike-engine") -> bool:
    """Restarts systemd service on Linux / Raspberry Pi."""
    try:
        cmd = ["sudo", "systemctl", "restart", service_name]
        logger.info("Executing service restart: %s", " ".join(cmd))
        subprocess.run(cmd, check=True, timeout=30)
        logger.info("Service '%s' restarted successfully.", service_name)
        return True
    except Exception as exc:
        logger.warning("Could not restart service '%s': %s", service_name, exc)
        return False


def main() -> None:
    args = parse_args()
    env_path = os.path.abspath(args.env)

    if not os.path.exists(env_path):
        logger.critical(".env file not found at: %s", env_path)
        sys.exit(1)

    cfg = dotenv_values(env_path)
    api_key = cfg.get("KITE_API_KEY", "").strip()
    api_secret = cfg.get("KITE_API_SECRET", "").strip()
    user_id = cfg.get("ZERODHA_USER_ID", "").strip()
    password = cfg.get("ZERODHA_PASSWORD", "").strip()
    totp_secret = cfg.get("ZERODHA_TOTP_SECRET", "").strip()
    webhook_url = cfg.get("DISCORD_WEBHOOK_URL", "").strip()

    notifier = DiscordNotifier(webhook_url=webhook_url) if webhook_url else None

    # Validate required credentials
    missing = []
    for name, val in [
        ("KITE_API_KEY", api_key),
        ("KITE_API_SECRET", api_secret),
        ("ZERODHA_USER_ID", user_id),
        ("ZERODHA_PASSWORD", password),
        ("ZERODHA_TOTP_SECRET", totp_secret),
    ]:
        if not val or "your_" in val:
            missing.append(name)

    if missing:
        msg = f"Auto-login failed: missing or default values for {', '.join(missing)} in .env"
        logger.error(msg)
        if notifier:
            notifier.error(msg)
        sys.exit(1)

    try:
        logger.info("Starting Zero-Touch automated morning login...")
        access_token = perform_automated_login(
            api_key=api_key,
            api_secret=api_secret,
            user_id=user_id,
            password=password,
            totp_secret=totp_secret,
        )

        # Save to .env
        save_access_token_to_env(env_path, access_token)
        logger.info("KITE_ACCESS_TOKEN updated successfully in %s", env_path)

        # Restart service if flag given or running on Linux
        restarted = False
        if args.restart_service or sys.platform.startswith("linux"):
            restarted = restart_systemd_service("nifty-strike-engine")

        now_str = datetime.now(tz=IST).strftime("%H:%M:%S IST")
        success_msg = (
            f"🏸 **Zero-Touch Morning Login Successful** ({now_str})\n"
            f"• User: `{user_id}`\n"
            f"• Access Token: Updated in `.env`\n"
            f"• Service Restart: {'✅ Completed' if restarted else 'ℹ️ Standalone'}\n"
            f"• Engine Status: Armed & Ready for Market Open (09:15 IST)"
        )
        logger.info(success_msg)
        if notifier:
            notifier.info(success_msg)

    except Exception as exc:
        err_msg = f"Zero-Touch Auto-Login Failed: {exc}"
        logger.exception(err_msg)
        if notifier:
            notifier.error(err_msg)
        sys.exit(1)


if __name__ == "__main__":
    main()

