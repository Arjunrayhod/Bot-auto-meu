"""
Main Orchestrator: Autonomous Academic Assistant Bot.
Coordinates authentication, dashboard polling, task processing, and scheduling.
"""

import os
import sys
import time
import signal
import logging
import argparse
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

# Ensure src package is in path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from src.auth import get_authenticated_session, perform_login
from src.monitor import scan_dashboard_and_process_tasks
from src.notifier import send_telegram_message, notify_fatal_alert
from src.telegram_bot import interactive_listener, send_menu
from src.email_listener import gmail_listener, check_gmail_for_meu_alerts

load_dotenv(override=True)

# Logging Configuration
_bot_handlers = [logging.FileHandler(os.path.join(BASE_DIR, "bot.log"), encoding="utf-8")]
if sys.stdout is not None:
    _bot_handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s]: %(message)s",
    handlers=_bot_handlers,
)
logger = logging.getLogger("academic_bot.main")

RUNNING = True


def handle_sigint(signum, frame):
    """Graceful termination handler."""
    global RUNNING
    logger.info("Shutdown signal received. Finishing active operations...")
    RUNNING = False


try:
    import threading
    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, handle_sigint)
        signal.signal(signal.SIGTERM, handle_sigint)
except (ValueError, AttributeError):
    pass


def run_single_cycle() -> int:
    """Executes a single check cycle against the college portal."""
    logger.info("Starting task discovery cycle...")
    new_tasks = 0

    with sync_playwright() as p:
        browser, context, page = get_authenticated_session(p)
        try:
            # 1. Scan Dashboard for Quizzes, Assignments, Practicals
            new_tasks = scan_dashboard_and_process_tasks(page)

            # 2. Check and cache Attendance + Low Attendance Alarm
            try:
                from src.attendance_processor import extract_attendance_data, format_low_attendance_alert
                att_data = extract_attendance_data(page)
                low_alert = format_low_attendance_alert(att_data)
                if low_alert:
                    send_telegram_message(low_alert, parse_mode="HTML")
            except Exception as e:
                logger.debug(f"Periodic attendance scan: {e}")

            # 3. Check Deadlines + Urgent Deadline Alarm
            try:
                from src.deadline_tracker import extract_assignments_deadlines, get_urgent_deadline_alert
                deadlines = extract_assignments_deadlines(page)
                urgent_alert = get_urgent_deadline_alert(deadlines)
                if urgent_alert:
                    send_telegram_message(urgent_alert, parse_mode="HTML")
            except Exception as e:
                logger.debug(f"Periodic deadline scan: {e}")

            # 4. Check & Sync Marks / MST
            try:
                from src.marks_processor import extract_marks_data
                extract_marks_data(page)
            except Exception as e:
                logger.debug(f"Periodic marks scan: {e}")

            # 5. Check Gmail for Early Quiz/Exam notices
            try:
                check_gmail_for_meu_alerts()
            except Exception as e:
                logger.debug(f"Periodic Gmail scan: {e}")

            logger.info(f"Cycle completed. {new_tasks} new task(s) processed.")
        finally:
            context.close()
            browser.close()

    return new_tasks


def ensure_single_bot():
    """Kills any older running daemon bot instance to avoid duplicate listeners."""
    import psutil
    me = os.getpid()
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.info['pid'] != me:
                cmdline = p.info.get('cmdline') or []
                if 'src.main' in cmdline and '--daemon' in cmdline and not any(arg == '-c' for arg in cmdline):
                    logger.warning(f"Terminating older duplicate bot instance (PID {p.info['pid']})...")
                    p.kill()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="Autonomous Academic Assistant Bot")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run one scan cycle and exit immediately.",
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously in background polling mode.",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=int(os.getenv("CHECK_INTERVAL_SECONDS", "900")),
        help="Polling interval in seconds between cycles (default: 900s / 15m).",
    )
    parser.add_argument(
        "--test-telegram",
        action="store_true",
        help="Send a test ping to Telegram to verify bot credentials.",
    )
    parser.add_argument(
        "--test-login",
        action="store_true",
        help="Test credentials and session creation without processing tasks.",
    )
    parser.add_argument(
        "--menu",
        action="store_true",
        help="Send interactive control dashboard with action buttons to Telegram.",
    )

    args = parser.parse_args()

    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")

    # Menu delivery command
    if args.menu:
        if chat_id:
            logger.info(f"Delivering interactive dashboard menu to chat {chat_id}...")
            send_menu(chat_id)
            logger.info("Menu delivered successfully!")
        else:
            logger.error("TELEGRAM_CHAT_ID not configured in .env.")
        return

    # Telegram self-test
    if args.test_telegram:
        logger.info("Testing Telegram connection...")
        ok = send_telegram_message("🤖 <b>Academic Assistant Bot Connected Successfully!</b>", parse_mode="HTML")
        if ok:
            logger.info("Telegram ping delivered successfully!")
        else:
            logger.error("Failed to send Telegram message. Check TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env.")
        return

    # Login self-test
    if args.test_login:
        logger.info("Performing login test...")
        with sync_playwright() as p:
            browser, context, page = get_authenticated_session(p)
            logger.info(f"Final landing URL: {page.url}")
            context.close()
            browser.close()
        logger.info("Login test finished.")
        return

    # If --daemon flag is provided or default continuous mode
    if args.daemon:
        ensure_single_bot()
        logger.info(f"Bot launched in continuous daemon mode. Polling interval: {args.interval}s")
        # Start background interactive button listener
        interactive_listener.start()
        # Start background Gmail sniper listener
        gmail_listener.start()
        # Start exact-time Quiz Sniper worker
        from src.sniper import quiz_sniper
        quiz_sniper.start()

        send_telegram_message(
            f"🚀 <b>Academic & Quiz Auto-Bot Activated</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 <b>AI Engine:</b> Gemini AI\n"
            f"🎯 <b>Exact-Time Quiz Sniper:</b> Active & Armed\n"
            f"⚡ <b>Auto Quiz Solve:</b> Enabled (80% Safety Timer)\n"
            f"📡 <b>Status:</b> Actively monitoring portal 24/7.",
            parse_mode="HTML",
        )
        try:
            while RUNNING:
                try:
                    run_single_cycle()
                except Exception as e:
                    logger.error(f"Unexpected error during polling cycle: {e}", exc_info=True)

                logger.info(f"Sleeping for {args.interval}s until next scan cycle...")
                sleep_elapsed = 0
                while sleep_elapsed < args.interval and RUNNING:
                    time.sleep(1)
                    sleep_elapsed += 1
        finally:
            interactive_listener.stop()
            gmail_listener.stop()
            quiz_sniper.stop()

        logger.info("Daemon gracefully exited.")
    else:
        # Single execution run
        run_single_cycle()
        # If running in cloud (GitHub Actions), check if an armed sniper is upcoming within 25 mins
        try:
            from src.sniper import quiz_sniper
            quiz_sniper.check_cloud_standby(max_wait_seconds=1500)
        except Exception as e:
            logger.debug(f"Cloud sniper standby check: {e}")


if __name__ == "__main__":
    main()
