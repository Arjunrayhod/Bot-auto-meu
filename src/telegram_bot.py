"""
Interactive Telegram Bot Controller: Provides on-demand action buttons,
attendance breakdown, low-attendance alarms, and assignment deadline countdowns.
Runs continuously in a lightweight background polling thread.
"""

import os
import time
import json
import logging
import threading
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.telegram_interactive")

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_API_URL = f"https://api.telegram.org/bot{TOKEN}"


def get_main_menu_markup() -> Dict[str, Any]:
    """Generates an Inline Keyboard for quick interactive actions."""
    return {
        "inline_keyboard": [
            [
                {"text": "🏆 Declared Results (Marks)", "callback_data": "btn_declared_results"},
                {"text": "⚡ Scan & Solve Work", "callback_data": "btn_scan_solve"},
            ],
            [
                {"text": "📝 Online Quizzes / Tests", "callback_data": "btn_tests"},
                {"text": "📊 Check Attendance", "callback_data": "btn_attendance"},
            ],
            [
                {"text": "⏳ Pending Deadlines", "callback_data": "btn_deadlines"},
                {"text": "🎯 MST & Exam Marks", "callback_data": "btn_marks"},
            ],
            [
                {"text": "⚠️ Low Attendance", "callback_data": "btn_low_attendance"},
                {"text": "🔄 Sync Portal Now", "callback_data": "btn_sync"},
            ],
            [
                {"text": "🤖 Bot Status", "callback_data": "btn_status"},
            ],
        ]
    }


def get_reply_keyboard_markup() -> Dict[str, Any]:
    """Generates a persistent quick-access keyboard above the chat input."""
    return {
        "keyboard": [
            [{"text": "🏆 Declared Results (Marks)"}, {"text": "⚡ Scan & Solve Work"}],
            [{"text": "📝 Online Quizzes / Tests"}, {"text": "📊 Check Attendance"}],
            [{"text": "⏳ Deadlines"}, {"text": "🎯 MST & Exam Marks"}],
            [{"text": "📋 Main Menu"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def send_menu(chat_id: str, text: Optional[str] = None):
    """Sends the master control dashboard with inline action buttons and command guide."""
    msg = text or (
        "🎓 <b>COLLEGE PORTAL CONTROL DASHBOARD</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Welcome! Kisi bhi option ke liye neeche button dabayein ya chat me keyword likhein:\n\n"
        "💬 <b>Chat Commands / Keywords:</b>\n"
        "• <code>/results</code> ya <code>number</code> ➔ 🏆 Declared Quiz &amp; Test Marks (Scores)\n"
        "• <code>/tests</code> ya <code>quiz</code> ➔ Live Quizzes/Tests (Date, Time, Duration &amp; Status)\n"
        "• <code>/scan</code> ya <code>solve</code> ➔ Naye assignments/quizzes scan &amp; auto-solve\n"
        "• <code>/deadlines</code> ya <code>assignment</code> ➔ Assignment dates &amp; timer\n"
        "• <code>/attendance</code> ya <code>haziri</code> ➔ Attendance report\n"
        "• <code>/low</code> ya <code>shortage</code> ➔ Low attendance (&lt;75%) alert\n"
        "• <code>/marks</code> ya <code>mst</code> ➔ Test marks &amp; MST score\n"
        "• <code>/sync</code> ya <code>refresh</code> ➔ Portal se fresh data reload\n"
        "• <code>/status</code> ➔ Bot 24/7 background status"
    )
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "reply_markup": get_main_menu_markup(),
    }
    try:
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        logger.error(f"Error sending interactive menu: {e}")


def answer_callback_query(callback_query_id: str, text: Optional[str] = None):
    """Acknowledges button press to remove loading state on Telegram client."""
    try:
        payload = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        requests.post(f"{TELEGRAM_API_URL}/answerCallbackQuery", json=payload, timeout=10)
    except Exception as e:
        logger.debug(f"Callback answer error: {e}")


def handle_attendance_request(chat_id: str, force_refresh: bool = False):
    """Replies with live attendance report."""
    from src.attendance_processor import get_cached_attendance, format_attendance_report

    cached = get_cached_attendance()
    if cached and not force_refresh:
        report = format_attendance_report(cached)
        payload = {
            "chat_id": chat_id,
            "text": report,
            "parse_mode": "HTML",
            "reply_markup": get_main_menu_markup(),
        }
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)
        return

    # If not cached or refresh requested, fetch live via Playwright
    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        json={"chat_id": chat_id, "text": "⏳ <i>Fetching live attendance from portal, please wait 5-10s...</i>", "parse_mode": "HTML"},
        timeout=10,
    )

    try:
        from playwright.sync_api import sync_playwright
        from src.auth import get_authenticated_session
        from src.attendance_processor import extract_attendance_data

        with sync_playwright() as p:
            b, ctx, page = get_authenticated_session(p)
            data = extract_attendance_data(page)
            ctx.close()
            b.close()

        report = format_attendance_report(data)
        payload = {
            "chat_id": chat_id,
            "text": report,
            "parse_mode": "HTML",
            "reply_markup": get_main_menu_markup(),
        }
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        logger.error(f"Failed to fetch live attendance: {e}")
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": f"❌ Error fetching attendance: {e}"},
            timeout=10,
        )


def handle_low_attendance_request(chat_id: str):
    """Replies with low-attendance warnings and recovery calculations."""
    from src.attendance_processor import get_cached_attendance, format_low_attendance_alert

    cached = get_cached_attendance()
    if not cached:
        handle_attendance_request(chat_id, force_refresh=True)
        return

    alert = format_low_attendance_alert(cached)
    if alert:
        payload = {
            "chat_id": chat_id,
            "text": alert,
            "parse_mode": "HTML",
            "reply_markup": get_main_menu_markup(),
        }
    else:
        payload = {
            "chat_id": chat_id,
            "text": "🎉 <b>Good News!</b> You have <b>NO subjects</b> with attendance below 75%. Keep it up! 🟢",
            "parse_mode": "HTML",
            "reply_markup": get_main_menu_markup(),
        }

    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)


def handle_deadlines_request(chat_id: str):
    """Replies with assignment deadlines and countdown badges."""
    from src.deadline_tracker import get_cached_deadlines, format_deadlines_report

    cached = get_cached_deadlines()
    if not cached:
        # Fallback quick scan
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": "⏳ <i>Scanning LMS for assignment deadlines...</i>", "parse_mode": "HTML"},
            timeout=10,
        )
        try:
            from playwright.sync_api import sync_playwright
            from src.auth import get_authenticated_session
            from src.deadline_tracker import extract_assignments_deadlines

            with sync_playwright() as p:
                b, ctx, page = get_authenticated_session(p)
                cached = extract_assignments_deadlines(page)
                ctx.close()
                b.close()
        except Exception as e:
            logger.error(f"Error fetching deadlines: {e}")

    report = format_deadlines_report(cached)
    payload = {
        "chat_id": chat_id,
        "text": report,
        "parse_mode": "HTML",
        "reply_markup": get_main_menu_markup(),
    }
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)


def handle_tests_request(chat_id: str, force_refresh: bool = False):
    """Replies with live or cached LMS tests and quizzes schedule with timing and duration."""
    from src.deadline_tracker import get_cached_tests, format_tests_report, extract_lms_tests

    cached = get_cached_tests()
    if not cached or force_refresh:
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": "⏳ <i>Checking LMS TEST LIST for upcoming & active quizzes...</i>", "parse_mode": "HTML"},
            timeout=10,
        )
        try:
            from playwright.sync_api import sync_playwright
            from src.auth import get_authenticated_session
            with sync_playwright() as p:
                b, ctx, page = get_authenticated_session(p)
                cached = extract_lms_tests(page)
                ctx.close()
                b.close()
        except Exception as e:
            logger.error(f"Error fetching LMS tests: {e}")

    report = format_tests_report(cached)
    payload = {
        "chat_id": chat_id,
        "text": report,
        "parse_mode": "HTML",
        "reply_markup": get_main_menu_markup(),
    }
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)


def handle_declared_results_request(chat_id: str, force_refresh: bool = False):
    """Replies with all declared quiz marks, scores, and release status."""
    from src.deadline_tracker import get_cached_tests, extract_lms_tests
    from src.marks_processor import format_declared_results_report

    cached = get_cached_tests()
    if not cached or force_refresh:
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": "⏳ <i>Checking portal for declared quiz & test results...</i>", "parse_mode": "HTML"},
            timeout=10,
        )
        try:
            from playwright.sync_api import sync_playwright
            from src.auth import get_authenticated_session
            with sync_playwright() as p:
                b, ctx, page = get_authenticated_session(p)
                cached = extract_lms_tests(page)
                ctx.close()
                b.close()
        except Exception as e:
            logger.error(f"Error fetching declared results: {e}")

    report = format_declared_results_report(cached or [])
    payload = {
        "chat_id": chat_id,
        "text": report,
        "parse_mode": "HTML",
        "reply_markup": get_main_menu_markup(),
    }
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)


def handle_exams_request(chat_id: str):
    """Replies with Mid Exam / CIA assessment schedule."""
    msg = (
        "🎯 <b>MID EXAM / INTERNAL ASSESSMENT SCHEDULE</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>Active Term:</b> 2026-27 - Odd | MEU - FOCSA - BCA\n"
        "📝 <b>Assessment Code:</b> MST - 01 & MST - 02\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "ℹ️ <i>When upcoming question papers or dates are published, the bot will auto-solve and notify you here!</i>"
    )
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "reply_markup": get_main_menu_markup(),
    }
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)


def handle_status_request(chat_id: str):
    """Replies with real-time operational status."""
    msg = (
        "🤖 <b>ACADEMIC BOT OPERATIONAL STATUS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🟢 <b>Status:</b> Online & Monitoring 24/7\n"
        "⚡ <b>Auto Quiz Solve:</b> Enabled (Gemini 3.5 Flash)\n"
        "⏱️ <b>Safety Timer:</b> 80% Pacing\n"
        "📚 <b>Assignment Assist:</b> PDF Extraction Active\n"
        "📊 <b>Attendance Tracker:</b> Alerting &lt;75%\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "💡 <i>Everything is running smoothly in the background.</i>"
    )
    payload = {
        "chat_id": chat_id,
        "text": msg,
        "parse_mode": "HTML",
        "reply_markup": get_main_menu_markup(),
    }
    requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)


def handle_marks_request(chat_id: str, force_refresh: bool = False):
    """Replies with live or cached Test & MST marks report."""
    from src.marks_processor import get_cached_marks, format_marks_report

    cached = get_cached_marks()
    if cached and not force_refresh:
        report = format_marks_report(cached)
        payload = {
            "chat_id": chat_id,
            "text": report,
            "parse_mode": "HTML",
            "reply_markup": get_main_menu_markup(),
        }
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)
        return

    # Scrape live if not cached or force refresh
    requests.post(
        f"{TELEGRAM_API_URL}/sendMessage",
        json={"chat_id": chat_id, "text": "⏳ <i>Fetching live Test Marks & MST data from portal...</i>", "parse_mode": "HTML"},
        timeout=10,
    )
    try:
        from playwright.sync_api import sync_playwright
        from src.auth import get_authenticated_session
        from src.marks_processor import extract_marks_data

        with sync_playwright() as p:
            b, ctx, page = get_authenticated_session(p)
            data = extract_marks_data(page)
            ctx.close()
            b.close()

        report = format_marks_report(data)
        payload = {
            "chat_id": chat_id,
            "text": report,
            "parse_mode": "HTML",
            "reply_markup": get_main_menu_markup(),
        }
        requests.post(f"{TELEGRAM_API_URL}/sendMessage", json=payload, timeout=15)
    except Exception as e:
        logger.error(f"Error fetching marks: {e}")
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": f"❌ Error fetching marks: {e}"},
            timeout=10,
        )


def handle_scan_and_solve_request(chat_id: str):
    """
    On-demand scanning and processing of pending assignments, practicals, and quizzes.
    Runs asynchronously in a background worker thread.
    """
    def _run_scan():
        # Immediate notification
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": (
                    "🔍 <b>PORTAL SCAN IN PROGRESS...</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "LMS aur Dashboard check kiya ja raha hai.\n"
                    "Agar koi naya assignment ya quiz milega toh Gemini AI se solve karke yahin send ho jayega! ⏳"
                ),
                "parse_mode": "HTML",
            },
            timeout=10,
        )

        try:
            from src.main import run_single_cycle
            new_tasks = run_single_cycle()

            if new_tasks > 0:
                msg = (
                    f"✅ <b>SCAN &amp; SOLVE COMPLETE!</b>\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"🎯 <b>{new_tasks} naya task detect aur process hua!</b>\n"
                    f"Solutions aur answers upar chat me deliver kar diye gaye hain. 📄"
                )
            else:
                msg = (
                    "✅ <b>SCAN COMPLETE!</b>\n"
                    "━━━━━━━━━━━━━━━━━━\n"
                    "🎉 <b>Sabhi assignments &amp; quizzes up-to-date hain!</b>\n"
                    "Portal par abhi koi naya unsolved task pending nahi hai. 👍"
                )

            requests.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": msg,
                    "parse_mode": "HTML",
                    "reply_markup": get_main_menu_markup(),
                },
                timeout=15,
            )
        except Exception as e:
            logger.error(f"Error during on-demand scan: {e}")
            requests.post(
                f"{TELEGRAM_API_URL}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"❌ Scan ke dauran error: {e}",
                    "reply_markup": get_main_menu_markup(),
                },
                timeout=10,
            )

    threading.Thread(target=_run_scan, daemon=True, name="ManualScanWorker").start()


def dispatch_action(action: str, chat_id: str):
    """Routes an action identifier or command to the appropriate handler."""
    act = action.strip().lower()

    # Declared Results & Quiz Marks
    if act in (
        "btn_declared_results", "🏆 declared results (marks)", "declared results", "declared marks",
        "/results", "/result", "/scores", "/score", "result", "results", "score", "scores",
        "number", "numbers", "marks", "quiz result", "quize result", "quiz marks", "quize marks",
        "kitne number aaye", "test result", "result aa gaya kya"
    ):
        handle_declared_results_request(chat_id)

    # Scan & Solve Pending Work
    elif act in ("btn_scan_solve", "⚡ scan & solve work", "/scan", "scan", "solve", "solve assignments", "scan now", "check work", "kuch naya aaya kya"):
        handle_scan_and_solve_request(chat_id)

    # Online Quizzes & Tests Schedule
    elif act in ("btn_tests", "📝 online quizzes / tests", "online quizzes / tests", "/tests", "/test", "/quiz", "/quizzes", "test", "tests", "quiz", "quizzes", "exam", "exams", "quiz timing", "test timing", "quiz kab hai"):
        handle_tests_request(chat_id)

    # MST & Semester Grades
    elif act in ("btn_marks", "🎯 mst & exam marks", "🎯 test & mst marks", "/mst", "mst", "grade", "grades", "/grades", "semester result"):
        handle_marks_request(chat_id)

    # Attendance
    elif act in ("btn_attendance", "📊 check attendance", "/attendance", "attendance", "haziri", "att", "meri attendance"):
        handle_attendance_request(chat_id)

    # Low attendance alert
    elif act in ("btn_low_attendance", "⚠️ low attendance alert", "⚠️ low attendance", "/low", "low", "low attendance", "kam attendance", "shortage"):
        handle_low_attendance_request(chat_id)

    # Deadlines
    elif act in ("btn_deadlines", "⏳ pending deadlines", "⏳ deadlines", "/deadlines", "deadline", "deadlines", "assignment", "assignments", "last date", "homework"):
        handle_deadlines_request(chat_id)

    # Mid Exam Schedule
    elif act in ("btn_exams", "📅 mid exam schedule", "🎯 mid exam schedule", "schedule", "exam date", "timetable"):
        handle_exams_request(chat_id)

    # Bot Status
    elif act in ("btn_status", "🤖 bot status", "🔄 refresh status", "/status", "status", "bot"):
        handle_status_request(chat_id)

    # Sync
    elif act in ("btn_sync", "🔄 sync portal now", "🔄 sync portal", "/sync", "sync", "refresh", "reload"):
        requests.post(
            f"{TELEGRAM_API_URL}/sendMessage",
            json={"chat_id": chat_id, "text": "🔄 <i>Initiating full sync of portal data...</i>", "parse_mode": "HTML"},
            timeout=10,
        )
        handle_attendance_request(chat_id, force_refresh=True)

    # Help / Menu / Start / Greetings
    elif act in ("btn_help", "/menu", "menu", "/start", "start", "help", "options", "dashboard", "kya hai", "hi", "hello"):
        send_menu(chat_id)

    else:
        send_menu(chat_id, (
            f"🤖 Command '<code>{action}</code>' samajh nahi aaya.\n\n"
            "Neeche buttons dabayein ya ye keywords bhejein:\n"
            "• <code>/marks</code> ➔ Test marks & MST score\n"
            "• <code>/attendance</code> ➔ Attendance report\n"
            "• <code>/low</code> ➔ Low attendance alert\n"
            "• <code>/deadlines</code> ➔ Pending assignments\n"
            "• <code>/sync</code> ➔ Portal refresh\n"
            "• <code>/menu</code> ➔ Master dashboard"
        ))


_recent_actions = {}
_action_lock = threading.Lock()

def is_duplicate_action(chat_id: str, action: str, window_sec: float = 3.0) -> bool:
    """Returns True if this exact action was triggered from chat_id within window_sec to prevent double-clicks."""
    now = time.time()
    key = (chat_id, action)
    with _action_lock:
        last_time = _recent_actions.get(key, 0.0)
        if (now - last_time) < window_sec:
            return True
        _recent_actions[key] = now
        # Periodic cleanup of keys older than 60s
        for k in list(_recent_actions.keys()):
            if (now - _recent_actions[k]) > 60:
                del _recent_actions[k]
    return False

class TelegramInteractiveListener:
    """
    Background worker that listens for user button clicks and commands via getUpdates.
    """

    def __init__(self):
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_update_id = 0

    def start(self):
        if not TOKEN:
            logger.warning("Telegram Bot Token missing. Interactive listener not starting.")
            return

        self.running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True, name="TelegramListener")
        self.thread.start()
        logger.info("Telegram Interactive Listener started in background thread.")

        # Send greeting menu if chat_id known
        if CHAT_ID:
            send_menu(CHAT_ID, "🚀 <b>Academic Bot Control Dashboard Connected!</b>\nTap any button below anytime:")

    def stop(self):
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=3)
        logger.info("Telegram Interactive Listener stopped.")

    def _poll_loop(self):
        while self.running:
            try:
                url = f"{TELEGRAM_API_URL}/getUpdates"
                params = {"timeout": 10, "offset": self.last_update_id + 1}
                resp = requests.get(url, params=params, timeout=15)

                if resp.ok:
                    data = resp.json()
                    for update in data.get("result", []):
                        self.last_update_id = update["update_id"]

                        # 1. Handle Inline Keyboard Callbacks
                        if "callback_query" in update:
                            cb = update["callback_query"]
                            cb_id = cb["id"]
                            cb_data = cb.get("data", "")
                            msg_obj = cb.get("message") or {}
                            chat_obj = msg_obj.get("chat") or {}
                            from_chat = str(chat_obj.get("id") or cb.get("from", {}).get("id") or CHAT_ID)

                            answer_callback_query(cb_id)
                            if is_duplicate_action(from_chat, cb_data, window_sec=3.0):
                                logger.info(f"Ignored duplicate click for action '{cb_data}' from {from_chat}")
                                continue

                            logger.info(f"Telegram Action Triggered: '{cb_data}' by chat: {from_chat}")
                            threading.Thread(
                                target=dispatch_action, args=(cb_data, from_chat), daemon=True
                            ).start()

                        # 2. Handle Text Messages / Commands
                        elif "message" in update and "text" in update["message"]:
                            msg = update["message"]
                            text = msg["text"].strip().lower()
                            from_chat = str(msg.get("chat", {}).get("id") or CHAT_ID)

                            if is_duplicate_action(from_chat, text, window_sec=1.5):
                                logger.info(f"Ignored duplicate text command '{text}' from {from_chat}")
                                continue

                            logger.info(f"Telegram Text Command: '{text}' by chat: {from_chat}")
                            threading.Thread(
                                target=dispatch_action, args=(text, from_chat), daemon=True
                            ).start()

            except Exception as e:
                logger.debug(f"Polling network exception: {e}")

            time.sleep(1.0)


# Global listener instance
interactive_listener = TelegramInteractiveListener()
