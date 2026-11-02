"""
Auth Module: Handles ASP.NET WebForms authentication, session persistence,
maintenance banner handling, and automatic session recovery.
"""

import os
import time
import logging
from typing import Optional, Tuple
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright, Browser, BrowserContext, Page, Response

from src.notifier import notify_server_error, notify_fatal_alert

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.auth")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AUTH_STATE_PATH = os.path.join(BASE_DIR, "state", "auth_state.json")

LOGIN_URL = os.getenv("PORTAL_LOGIN_URL", "https://meu.gnums.co.in/Login.aspx")
DASHBOARD_URL = os.getenv("PORTAL_DASHBOARD_URL", "https://meu.gnums.co.in/StudentPanel/StudentDashboard.aspx")
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
SERVER_ERROR_WAIT_SECONDS = int(os.getenv("SERVER_ERROR_WAIT_SECONDS", "300"))
HEADLESS_MODE = os.getenv("HEADLESS", "False").lower() in ("true", "1", "yes")


def apply_stealth_if_available(page: Page):
    """Applies playwright-stealth evasion scripts if the package is installed."""
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
        logger.debug("Stealth evasions applied.")
    except Exception as e:
        logger.debug(f"playwright-stealth not active or error applying: {e}")


def dismiss_maintenance_banner_if_present(page: Page):
    """
    Rule: IGNORE the '11:55 PM to 12:30 AM IST' banner. Login works. Proceed.
    Dismisses or ignores any modal, overlay, or alert banner if it impedes interaction.
    """
    try:
        # Common selectors for modal dismiss buttons, banners, or alert close buttons
        dismiss_candidates = [
            "button:has-text('Close')",
            "button:has-text('OK')",
            "button:has-text('Dismiss')",
            ".modal .close",
            "#btnCloseModal",
            ".alert-dismissible .close",
            "button[aria-label='Close']",
        ]
        for sel in dismiss_candidates:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=1500)
                logger.info("Dismissed maintenance / notice modal dialog.")
                page.wait_for_timeout(500)
                break
    except Exception as e:
        logger.debug(f"Maintenance check passed cleanly: {e}")


def is_server_error_status(status_code: int) -> bool:
    """Checks whether the HTTP response is an ASP.NET / IIS server error."""
    return status_code in (500, 502, 503, 504)


def perform_login(page: Page) -> bool:
    """
    Executes the ASP.NET WebForms login sequence.
    Handles __VIEWSTATE, __EVENTVALIDATION, Role radio button, Username, and Password.
    """
    username = os.getenv("PORTAL_USERNAME", "").strip()
    password = os.getenv("PORTAL_PASSWORD", "").strip()
    role = os.getenv("PORTAL_ROLE", "Student").strip()

    if not username or not password:
        logger.error("PORTAL_USERNAME or PORTAL_PASSWORD is not set in .env!")
        return False

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logger.info(f"Navigating to login page: {LOGIN_URL} (Attempt {attempt}/{MAX_RETRIES})")
            response = page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1000)
            try:
                page.wait_for_selector("input[type='password']", timeout=15000)
            except Exception:
                pass

            # Check server error codes (500/502/503)
            if response and is_server_error_status(response.status):
                logger.warning(f"Server returned status {response.status}. Portal may be down.")
                notify_server_error(attempt, MAX_RETRIES, response.status, SERVER_ERROR_WAIT_SECONDS // 60)
                if attempt < MAX_RETRIES:
                    time.sleep(SERVER_ERROR_WAIT_SECONDS)
                    continue
                else:
                    notify_fatal_alert(f"Portal unreachable after {MAX_RETRIES} attempts. Status: {response.status}")
                    return False

            # Ignore maintenance banner rule
            dismiss_maintenance_banner_if_present(page)

            # 1. Select Role = 'Student'
            logger.info(f"Selecting Role: {role}")
            role_selected = False
            radio_selectors = [
                "#rblRole_1",
                "label:has-text('Student')",
                "input[id*='rblRole_1']",
                f"input[type='radio'][value*='{role}']",
                f"input[type='radio'][id*='{role}']",
            ]
            for sel in radio_selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0:
                        loc.first.click(timeout=3000)
                        role_selected = True
                        logger.info(f"Role '{role}' selected using selector: {sel}")
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        page.wait_for_timeout(2000)
                        break
                except Exception:
                    continue

            if not role_selected:
                logger.warning("Could not explicitly click radio selector for Student. Proceeding with default radio.")

            page.wait_for_timeout(500)

            # 2. Fill Username
            user_selectors = [
                "#txtUsername",
                "input[name='txtUsername']",
                "input[id*='txtUsername']",
                "input[placeholder*='Mobile']",
                "input[id*='txtUserName']",
                "input[type='text']",
            ]
            user_filled = False
            for sel in user_selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.fill(username, timeout=3000)
                        user_filled = True
                        logger.info(f"Filled username using selector: {sel}")
                        break
                except Exception:
                    continue

            if not user_filled:
                logger.error("Could not find Username input field.")
                return False

            # 3. Fill Password
            pw_loc = page.locator("#txtPassword, input[type='password']").first
            pw_loc.fill(password, timeout=3000)
            page.wait_for_timeout(300)

            # 4. Click Submit Button & Handle ASP.NET PostBack
            logger.info("Submitting login form...")
            submit_selectors = [
                "input[type='submit'][id*='btnLogin']",
                "button[id*='btnLogin']",
                "input[value*='Login']",
                "button:has-text('Login')",
                "input[type='submit']",
            ]
            submitted = False
            for sel in submit_selectors:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        logger.info(f"Clicking login button: {sel}")
                        loc.first.click()
                        try:
                            page.wait_for_load_state("domcontentloaded", timeout=15000)
                        except Exception:
                            pass
                        page.wait_for_timeout(3000)
                        submitted = True
                        break
                except Exception as e:
                    logger.debug(f"Submit attempt error with {sel}: {e}")
                    continue

            if not submitted:
                logger.error("Failed to click Login submit button.")
                return False

            # 5. Verify Successful Login
            current_url = page.url
            logger.info(f"Post-login URL: {current_url}")

            if "Dashboard" in current_url or "StudentPanel" in current_url:
                logger.info("Login successful! Redirected to Student Dashboard.")
                # Save storage state
                os.makedirs(os.path.dirname(AUTH_STATE_PATH), exist_ok=True)
                page.context.storage_state(path=AUTH_STATE_PATH)
                logger.info(f"Session cookies saved to: {AUTH_STATE_PATH}")
                return True

            # If still on Login.aspx, check for ASP.NET validation error labels
            error_lbl = page.locator("span[id*='lblError'], .alert-danger, .error")
            if error_lbl.count() > 0 and error_lbl.first.is_visible():
                error_text = error_lbl.first.inner_text().strip()
                logger.error(f"Login failed with portal message: '{error_text}'")
                notify_fatal_alert(f"Login rejected by portal: {error_text}")
                return False

        except Exception as e:
            logger.error(f"Exception during login attempt {attempt}: {e}")
            if attempt < MAX_RETRIES:
                time.sleep(5)
            else:
                notify_fatal_alert(f"Login crashed after {MAX_RETRIES} attempts: {e}")
                return False

    return False


def get_authenticated_session(playwright_instance) -> Tuple[Browser, BrowserContext, Page]:
    """
    Returns an authenticated Playwright BrowserContext and Page.
    1. Tests stored session in auth_state.json.
    2. If invalid or expired, automatically re-authenticates.
    """
    launch_kwargs = {
        "headless": HEADLESS_MODE,
        "args": ["--disable-blink-features=AutomationControlled", "--no-sandbox"],
    }
    try:
        browser: Browser = playwright_instance.chromium.launch(channel="chrome", **launch_kwargs)
    except Exception:
        browser: Browser = playwright_instance.chromium.launch(**launch_kwargs)

    context: BrowserContext
    if os.path.exists(AUTH_STATE_PATH) and os.path.getsize(AUTH_STATE_PATH) > 10:
        try:
            logger.info("Loading existing session state from auth_state.json...")
            context = browser.new_context(storage_state=AUTH_STATE_PATH)
        except Exception as e:
            logger.warning(f"Error loading auth_state.json ({e}). Creating fresh context.")
            context = browser.new_context()
    else:
        context = browser.new_context()

    page = context.new_page()
    apply_stealth_if_available(page)

    # Validate active session by checking Dashboard
    logger.info(f"Checking session validity at: {DASHBOARD_URL}")
    try:
        res = page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1000)
        dismiss_maintenance_banner_if_present(page)

        # Check if redirected back to Login.aspx
        if "Login.aspx" in page.url or (res and res.status in (401, 403)):
            logger.info("Session expired or unauthenticated. Performing fresh login...")
            if not perform_login(page):
                raise RuntimeError("Failed to re-authenticate with student credentials.")
        else:
            logger.info("Existing session is active and valid.")
    except Exception as e:
        logger.warning(f"Session check error ({e}). Re-authenticating...")
        if not perform_login(page):
            raise RuntimeError("Failed to authenticate with student credentials.")

    return browser, context, page
