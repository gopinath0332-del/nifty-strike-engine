"""
scripts/auto_login.py
=====================
Zero-Touch Automated Morning Authentication for Zerodha Kite Connect using
Headless Chromium (Playwright) + PyOTP (RFC 6238).

Bypasses Zerodha's anti-bot restrictions on raw HTTP 2FA endpoints by driving
a real headless browser session with genuine DOM events.

Flow:
  1. Reads credentials (API Key, API Secret, User ID, Password, TOTP Secret) from .env.
  2. Launches headless Chromium (with --no-sandbox flags for Raspberry Pi / root).
  3. Navigates to Kite Connect login portal.
  4. Inputs user ID & password, submits login form.
  5. Computes live TOTP code via PyOTP and fills 2FA input.
  6. Intercepts redirect callback to capture `request_token`.
  7. Exchanges `request_token` with Kite API Secret to generate daily `access_token`.
  8. Automatically updates KITE_ACCESS_TOKEN in .env.
  9. Restarts systemd service 'nifty-strike-engine' (if running on Raspberry Pi / Linux).
 10. Dispatches Discord notification with token refresh status.

Usage:
  python scripts/auto_login.py
  python scripts/auto_login.py --restart-service
"""

from __future__ import annotations

import argparse
import logging
import os
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
from dotenv import dotenv_values
from playwright.sync_api import sync_playwright

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
    parser = argparse.ArgumentParser(description="Zero-Touch Zerodha Auto-Login via Playwright & TOTP")
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
    parser.add_argument(
        "--headless",
        action="store_true",
        default=True,
        help="Run browser in headless mode (default: True)",
    )
    return parser.parse_args()


def perform_automated_login(
    api_key: str,
    api_secret: str,
    user_id: str,
    password: str,
    totp_secret: str,
    headless: bool = True,
) -> str:
    """
    Automates Kite Connect login via Headless Chromium and TOTP, returning a valid access_token.
    """
    captured_request_token: str | None = None
    captured_error: str | None = None

    login_url = f"https://kite.zerodha.com/connect/login?v=3&api_key={api_key}"
    logger.info("Launching Playwright browser (headless=%s)...", headless)

    # Chromium launch flags optimized for headless Raspberry Pi / Linux / Windows
    launch_args = [
        "--no-sandbox",
        "--disable-setuid-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ]

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, args=launch_args)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()

        # Intercept network requests to capture redirect URL even if localhost isn't listening
        def handle_request(request) -> None:
            nonlocal captured_request_token, captured_error
            req_url = request.url
            if "request_token=" in req_url:
                parsed = urllib.parse.urlparse(req_url)
                params = urllib.parse.parse_qs(parsed.query)
                tokens = params.get("request_token", [])
                if tokens:
                    captured_request_token = tokens[0]
                    logger.debug("Intercepted request_token: %s...", captured_request_token[:8])
            elif "status=error" in req_url:
                parsed = urllib.parse.urlparse(req_url)
                params = urllib.parse.parse_qs(parsed.query)
                msg = params.get("message", ["Unknown login error"])[0]
                captured_error = msg

        page.on("request", handle_request)

        try:
            logger.info("Navigating to Kite login portal...")
            page.goto(login_url, wait_until="domcontentloaded", timeout=30000)

            # Wait for User ID input
            logger.info("Entering credentials for user: %s", user_id)
            page.wait_for_selector("#userid", timeout=20000)
            page.fill("#userid", user_id)
            page.fill("#password", password)
            page.click("button[type='submit']")

            # Wait for 2FA screen or error message
            logger.info("Awaiting 2FA prompt...")
            # Check for invalid credentials error
            error_loc = page.locator(".error, .alert-error, div.message-error")
            for _ in range(20):
                if page.locator("input[type='number'], input[id='userid']").is_visible():
                    break
                if error_loc.is_visible():
                    err_txt = error_loc.inner_text()
                    raise RuntimeError(f"Step 1 (Credentials) rejected by Kite: {err_txt}")
                page.wait_for_timeout(250)

            # Generate TOTP code
            totp = pyotp.TOTP(totp_secret.replace(" ", "").strip())
            pin = totp.now()
            logger.info("Generated TOTP code: %s", pin)

            # Fill TOTP into number input (Kite auto-submits on 6th digit)
            twofa_input = page.locator("input[type='number']").first
            twofa_input.wait_for(state="visible", timeout=10000)
            twofa_input.fill(pin)
            logger.info("TOTP entered. Awaiting OAuth redirect...")

            # Poll for captured_request_token
            for _ in range(80):  # up to 16 seconds
                if captured_request_token:
                    break
                if captured_error:
                    raise RuntimeError(f"Kite auth returned error: {captured_error}")
                page.wait_for_timeout(200)

            if not captured_request_token:
                # Capture screenshot for troubleshooting if failure occurs
                debug_dir = os.path.join(os.path.dirname(__file__), "..", "data")
                os.makedirs(debug_dir, exist_ok=True)
                debug_img = os.path.join(debug_dir, "login_failure.png")
                page.screenshot(path=debug_img)
                raise RuntimeError(
                    f"Authentication timed out without capturing request_token. "
                    f"Final URL: {page.url}. Screenshot saved to {debug_img}"
                )

        finally:
            browser.close()

    logger.info("Request token acquired: %s...", captured_request_token[:8])

    # Exchange request_token for daily access_token
    logger.info("Exchanging request_token for Kite access_token...")
    access_token = generate_access_token(api_key, api_secret, captured_request_token)
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
        logger.info("Starting Zero-Touch automated morning login via Headless Chromium...")
        access_token = perform_automated_login(
            api_key=api_key,
            api_secret=api_secret,
            user_id=user_id,
            password=password,
            totp_secret=totp_secret,
            headless=args.headless,
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
