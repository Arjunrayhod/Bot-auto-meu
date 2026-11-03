"""
Quiz Sniper Engine:
Listens for Gmail advance schedule notices, calculates the exact quiz start time,
arms a precise countdown timer, and fires autonomous Playwright solve & submit
at the exact second the quiz opens.
"""

import os
import re
import sys
import json
import time
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

logger = logging.getLogger("academic_bot.sniper")
SNIPER_STATE_PATH = os.path.join(BASE_DIR, "state", "armed_snipers.json")


def parse_target_datetime(date_str: str, time_str: str) -> Optional[datetime]:
    """Parses portal/email date and time string into a valid datetime object."""
    clean_date = re.sub(r"\s+", " ", date_str).strip()
    clean_time = re.sub(r"\s+", " ", time_str).strip()

    m = re.search(r"(\d{1,2}:\d{2}\s*(?:AM|PM))", clean_time, re.IGNORECASE)
    if m:
        clean_time = m.group(1).upper()

    for fmt in ("%d-%m-%Y %I:%M %p", "%d/%m/%Y %I:%M %p", "%Y-%m-%d %I:%M %p"):
        try:
            return datetime.strptime(f"{clean_date} {clean_time}", fmt)
        except ValueError:
            continue
    return None


class QuizSniper:
    """Manages armed countdowns and exact-time autonomous quiz solving."""

    def __init__(self, state_path: str = SNIPER_STATE_PATH):
        self.state_path = state_path
        self.armed_targets: List[Dict[str, Any]] = []
        self.lock = threading.Lock()
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.load_state()

    def load_state(self):
        if os.path.exists(self.state_path):
            try:
                with open(self.state_path, "r", encoding="utf-8") as f:
                    self.armed_targets = json.load(f)
            except Exception as e:
                logger.error(f"Error loading sniper state: {e}")
                self.armed_targets = []
        else:
            self.armed_targets = []

    def save_state(self):
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        try:
            with open(self.state_path, "w", encoding="utf-8") as f:
                json.dump(self.armed_targets, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving sniper state: {e}")

    def arm(self, test_info: Dict[str, str]) -> bool:
        """
        Arms a sniper timer from email info:
        {'course': '...', 'test_name': 'Quiz 3', 'date': '03-11-2026', 'time': '03:00 PM', 'duration': '20 Min.'}
        """
        from src.notifier import send_telegram_message

        date_str = test_info.get("date", "").strip()
        time_str = test_info.get("time", "").strip()
        if not date_str or not time_str:
            logger.warning(f"Cannot arm sniper: missing date ({date_str}) or time ({time_str}).")
            return False

        target_dt = parse_target_datetime(date_str, time_str)
        if not target_dt:
            logger.warning(f"Failed to parse datetime for: {date_str} {time_str}")
            return False

        now = datetime.now()
        if target_dt < now:
            logger.info(f"Target time {target_dt} is in the past. Skipping arming.")
            return False

        key = f"{test_info.get('test_name')}:{test_info.get('course')}:{target_dt.strftime('%Y-%m-%d %H:%M')}"

        with self.lock:
            # Check if already armed
            for t in self.armed_targets:
                if t.get("key") == key and t.get("status") in ("armed", "completed"):
                    logger.debug(f"Sniper already armed or completed for: {key}")
                    return False

            sniper_entry = {
                "key": key,
                "course": test_info.get("course"),
                "test_name": test_info.get("test_name"),
                "date": date_str,
                "time": time_str,
                "duration": test_info.get("duration", "30 Min."),
                "target_iso": target_dt.isoformat(),
                "status": "armed",
                "armed_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self.armed_targets.append(sniper_entry)
            self.save_state()

        diff_seconds = (target_dt - now).total_seconds()
        diff_hours = int(diff_seconds // 3600)
        diff_mins = int((diff_seconds % 3600) // 60)

        logger.info(f"🎯 QUIZ SNIPER ARMED: {test_info.get('test_name')} in {diff_hours}h {diff_mins}m at {target_dt}")

        # Send Telegram confirmation
        arm_msg = (
            "🎯 <b>QUIZ SNIPER ARMED &amp; LOCKED!</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📚 <b>Subject:</b> {test_info.get('course')}\n"
            f"📝 <b>Quiz:</b> <b>{test_info.get('test_name')}</b>\n"
            f"📅 <b>Target Date:</b> <code>{date_str}</code>\n"
            f"⏰ <b>Start Time:</b> <code>{time_str}</code>\n"
            f"⏱️ <b>Duration:</b> {test_info.get('duration')}\n"
            f"⏳ <b>Countdown:</b> {diff_hours} hrs {diff_mins} mins remaining\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "💤 <b>Sleeping Strategy:</b>\n"
            f"• Bot ab target time tak bilkul shant rahega.\n"
            f"• Theek <code>{time_str}</code> par sniper auto-wake karega, Gemini AI se test solve karega, aur submit karega!"
        )
        send_telegram_message(arm_msg, parse_mode="HTML")

        # Also register Windows scheduled one-time task if on Windows
        try:
            trigger_time = (target_dt - timedelta(minutes=1)).strftime("%H:%M")
            trigger_date = target_dt.strftime("%d/%m/%Y")
            task_name = f"Sniper_{re.sub(r'[^a-zA-Z0-9]', '', test_info.get('test_name', 'Quiz'))[:12]}"
            vbs_path = os.path.join(BASE_DIR, "run_silent_background.vbs")
            cmd = f'schtasks /create /tn "{task_name}" /tr "wscript.exe \\"{vbs_path}\\"" /sc once /st {trigger_time} /sd {trigger_date} /f'
            import subprocess
            subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logger.info(f"Registered Windows OS Wakeup task '{task_name}' for {trigger_time} on {trigger_date}")
        except Exception as e:
            logger.debug(f"OS scheduled task notice: {e}")

        return True

    def start(self):
        """Starts background sniper countdown monitor loop."""
        if self.running:
            return
        self.running = True
        self.thread = threading.Thread(target=self._monitor_loop, daemon=True, name="QuizSniperWorker")
        self.thread.start()
        logger.info("Quiz Sniper monitor worker started.")

    def stop(self):
        self.running = False

    def _monitor_loop(self):
        while self.running:
            try:
                now = datetime.now()
                with self.lock:
                    for target in self.armed_targets:
                        if target.get("status") != "armed":
                            continue

                        target_dt = datetime.fromisoformat(target["target_iso"])

                        # Trigger if within 1 minute of start time, up to 15 minutes after
                        if now >= (target_dt - timedelta(minutes=1)) and now <= (target_dt + timedelta(minutes=25)):
                            logger.info(f"🚀 SNIPER TIME REACHED: Firing quiz solver for {target.get('test_name')}!")
                            target["status"] = "triggered"
                            self.save_state()

                            # Fire solving in independent thread
                            threading.Thread(
                                target=self._execute_sniper_run,
                                args=(target,),
                                daemon=True,
                                name=f"SniperExec_{target.get('test_name')}",
                            ).start()

                        # Expire if target time is more than 30 minutes in the past
                        elif now > (target_dt + timedelta(minutes=30)):
                            target["status"] = "expired"
                            self.save_state()

            except Exception as e:
                logger.error(f"Error in sniper monitor loop: {e}")

            time.sleep(10)

    def _execute_sniper_run(self, target: Dict[str, Any]):
        """Executes the autonomous login and test solver at the exact time."""
        from src.notifier import send_telegram_message
        from playwright.sync_api import sync_playwright
        from src.auth import get_authenticated_session
        from src.quiz_processor import process_quiz
        from src.monitor import _generate_task_id, ledger

        test_name = target.get("test_name", "Quiz")
        subject = target.get("course", "Academic Subject")

        # Telegram trigger alert
        send_telegram_message(
            f"⚡ <b>QUIZ SNIPER TRIGGERED RIGHT ON TIME!</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Test:</b> <b>{test_name}</b>\n"
            f"📚 <b>Subject:</b> {subject}\n"
            f"🚀 <b>Status:</b> Scheduled time aa gaya hai! Bot portal par live login karke test attempt kar raha hai... ⏳",
            parse_mode="HTML",
        )

        success = False
        try:
            with sync_playwright() as p:
                browser, context, page = get_authenticated_session(p)
                lms_url = "https://meu.gnums.co.in/StudentPanel/LMS/LMS_ContentStudentDashboard.aspx"
                logger.info(f"Sniper navigating to LMS Dashboard: {lms_url}")
                page.goto(lms_url, wait_until="networkidle", timeout=35000)

                # Look for the active test row in TEST LIST
                rows = page.locator("table tr")
                attempt_clicked = False

                for i in range(rows.count()):
                    row = rows.nth(i)
                    r_text = row.inner_text().strip()
                    if not r_text or "Sr." in r_text:
                        continue

                    action_btns = row.locator("a, button, input[type='submit']")
                    if action_btns.count() == 0:
                        continue

                    btn_txt = action_btns.first.inner_text().strip().lower()
                    btn_href = action_btns.first.get_attribute("href") or ""
                    is_start_btn = (any(w in btn_txt for w in ["start", "attempt", "click here"]) or "start" in btn_href.lower()) and "not attempted" not in btn_txt and "over" not in r_text.lower()

                    if is_start_btn:
                        logger.info(f"Sniper found active attempt button in row {i}: {r_text[:60]}")
                        action_btn = action_btns.first
                        task_id = _generate_task_id(test_name, "quizzes", subject)

                        task_page = context.new_page()
                        action_href = action_btn.get_attribute("href") or ""
                        if action_href and not action_href.startswith("javascript"):
                            task_page.goto(urljoin(page.url, action_href), wait_until="networkidle", timeout=35000)
                        else:
                            action_btn.click()
                            page.wait_for_timeout(3000)
                            task_page = page

                        success = process_quiz(task_page, task_id, test_name, subject=subject)
                        if success:
                            ledger.mark_seen("quizzes", task_id, test_name, status="completed")
                        if task_page != page:
                            task_page.close()

                        attempt_clicked = True
                        break

                if not attempt_clicked:
                    logger.warning(f"Sniper could not find active 'Start Test' link on LMS for {test_name}. Will retry in 60s...")
                    # Wait 60s and try one more refresh in case professor opened it slightly late
                    page.wait_for_timeout(60000)
                    page.reload(wait_until="networkidle")
                    # Check again
                    for i in range(rows.count()):
                        row = rows.nth(i)
                        r_text = row.inner_text().strip()
                        if any(w in r_text.lower() for w in ["start test", "attempt", "click here to start"]):
                            action_btn = row.locator("a, button, input[type='submit']").first
                            task_id = _generate_task_id(test_name, "quizzes", subject)
                            task_page = context.new_page()
                            action_href = action_btn.get_attribute("href") or ""
                            if action_href and not action_href.startswith("javascript"):
                                task_page.goto(urljoin(page.url, action_href), wait_until="networkidle", timeout=35000)
                            else:
                                action_btn.click()
                                page.wait_for_timeout(3000)
                                task_page = page
                            success = process_quiz(task_page, task_id, test_name, subject=subject)
                            if success:
                                ledger.mark_seen("quizzes", task_id, test_name, status="completed")
                            if task_page != page:
                                task_page.close()
                            break

                context.close()
                browser.close()

            target["status"] = "completed" if success else "failed"
            self.save_state()

        except Exception as e:
            logger.error(f"Error executing sniper run: {e}", exc_info=True)
            target["status"] = f"error: {e}"
            self.save_state()


# Global sniper singleton
quiz_sniper = QuizSniper()
