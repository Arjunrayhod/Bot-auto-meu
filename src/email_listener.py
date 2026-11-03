"""
Gmail Early Warning & Quiz Sniper Listener:
Monitors inbox for automated notices from noreply.meu@gnums.in.
Extracts test schedule (Course, Quiz Name, Date, Time, Duration),
sends instant Telegram advance alerts, and arms the autonomous solver.
"""

import os
import re
import time
import json
import email
import imaplib
import logging
import threading
from email.header import decode_header
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

from src.notifier import send_telegram_message

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.email_listener")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEN_EMAILS_PATH = os.path.join(BASE_DIR, "state", "seen_emails.json")

GMAIL_USER = os.getenv("GMAIL_USER", os.getenv("PORTAL_USERNAME", "")).strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
GMAIL_IMAP_SERVER = os.getenv("GMAIL_IMAP_SERVER", "imap.gmail.com")

MEU_SENDER = "noreply.meu@gnums.in"


def load_seen_email_ids() -> set:
    """Loads previously processed email message IDs to prevent duplicate alerts."""
    import json
    if os.path.exists(SEEN_EMAILS_PATH):
        try:
            with open(SEEN_EMAILS_PATH, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()


def save_seen_email_id(msg_id: str):
    """Saves processed email message ID to disk."""
    import json
    seen = load_seen_email_ids()
    seen.add(msg_id)
    os.makedirs(os.path.dirname(SEEN_EMAILS_PATH), exist_ok=True)
    try:
        with open(SEEN_EMAILS_PATH, "w", encoding="utf-8") as f:
            json.dump(list(seen), f, indent=2)
    except Exception as e:
        logger.error(f"Failed to save seen email ID: {e}")


def decode_str(s: Any) -> str:
    """Helper to decode MIME encoded headers."""
    if not s:
        return ""
    decoded_list = decode_header(s)
    text = ""
    for content, charset in decoded_list:
        if isinstance(content, bytes):
            text += content.decode(charset or "utf-8", errors="replace")
        else:
            text += str(content)
    return text.strip()


def clean_html_to_text(html_text: str) -> str:
    """Converts HTML email payload into clean plain text with preserved line breaks."""
    import html as html_lib
    h = html_text
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</?(?:p|tr|div|h\d)[^>]*>", "\n", h)
    h = re.sub(r"(?i)<td[^>]*>", " ", h)
    h = re.sub(r"<[^>]+>", "", h)
    h = html_lib.unescape(h)
    h = h.replace("\xa0", " ").replace("\u00a0", " ")
    return h


def extract_email_body(msg: email.message.Message) -> str:
    """Extracts text content from email message, handling multipart and HTML-only formats."""
    body_text = ""
    html_text = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition") or "")
            if "attachment" in cd:
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if ct == "text/plain":
                body_text += decoded + "\n"
            elif ct == "text/html":
                html_text += decoded + "\n"
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            charset = msg.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="replace")
            if msg.get_content_type() == "text/html":
                html_text = decoded
            else:
                body_text = decoded

    final_text = body_text.strip()
    if not final_text and html_text:
        final_text = clean_html_to_text(html_text).strip()
    return final_text


def parse_quiz_schedule_email(subject: str, body: str) -> Optional[Dict[str, str]]:
    """
    Parses MEU GNUMS email structure:
    Test Name :- Quiz 4
    Test Date :- 02-11-2026
    Test Time :- 02:10 PM
    Test Duration :- 30 Min.
    """
    clean_body = body.replace("\xa0", " ").replace("\u00a0", " ")
    clean_subject = subject.replace("\xa0", " ").replace("\u00a0", " ")

    if "test has been scheduled" not in clean_body.lower() and "test schedule" not in clean_subject.lower():
        return None

    data: Dict[str, str] = {
        "subject_raw": subject,
        "course": "University Course",
        "test_name": "Scheduled Quiz/Test",
        "date": "",
        "time": "",
        "duration": "",
    }

    # Extract Course
    course_match = re.search(r"Test\s*Schedule\s*for\s*:-\s*([^\n\r]+)", clean_subject, re.IGNORECASE)
    if not course_match:
        course_match = re.search(r"([A-Za-z0-9\-]+\s*-\s*[^\n\r]+?)\s+Test\s+has\s+been\s+Scheduled", clean_body, re.IGNORECASE)
    if course_match:
        data["course"] = re.sub(r"\s+", " ", course_match.group(1)).strip()

    # Extract Test Name
    test_match = re.search(r"Test\s*Name\s*:-\s*([^\n\r]+)", clean_body, re.IGNORECASE)
    if test_match:
        data["test_name"] = re.sub(r"\s+", " ", test_match.group(1)).strip()

    # Extract Test Date
    date_match = re.search(r"Test\s*Date\s*:-\s*([^\n\r]+)", clean_body, re.IGNORECASE)
    if date_match:
        data["date"] = re.sub(r"\s+", " ", date_match.group(1)).strip()

    # Extract Test Time
    time_match = re.search(r"Test\s*Time\s*:-\s*([^\n\r]+)", clean_body, re.IGNORECASE)
    if time_match:
        data["time"] = re.sub(r"\s+", " ", time_match.group(1)).strip()

    # Extract Test Duration
    dur_match = re.search(r"Test\s*Duration\s*:-\s*([^\n\r]+)", clean_body, re.IGNORECASE)
    if dur_match:
        data["duration"] = re.sub(r"\s+", " ", dur_match.group(1)).strip()

    return data


def format_quiz_email_alert(info: Dict[str, str]) -> str:
    """Formats a high-priority advance warning notification for Telegram."""
    lines = [
        "🚨 <b>UPCOMING QUIZ SCHEDULED (GMAIL ALERT)!</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"📚 <b>Subject:</b> {info.get('course', 'Course')}",
        f"📝 <b>Test Name:</b> <b>{info.get('test_name', 'Quiz')}</b>",
        f"📅 <b>Date:</b> <code>{info.get('date', 'Today')}</code>",
        f"⏰ <b>Start Time:</b> <code>{info.get('time', 'TBD')}</code>",
        f"⏱️ <b>Duration:</b> {info.get('duration', '30 Min.')}",
        "━━━━━━━━━━━━━━━━━━",
        "🎯 <b>Auto-Action Armed:</b>",
        "• Bot alert milte hi scheduled tracking me add ho gaya hai.",
        "• Scheduled time par bot auto-login karega, <b>Gemini AI se solve karega</b>, aur 80% safety timer ke sath submit karega!",
    ]
    return "\n".join(lines)


def check_gmail_for_meu_alerts() -> int:
    """
    Connects to Gmail via IMAP, searches for recent emails from noreply.meu@gnums.in,
    and dispatches Telegram alerts for any new quiz/exam announcements.
    Returns count of new alerts processed.
    """
    global GMAIL_USER, GMAIL_APP_PASSWORD
    GMAIL_USER = os.getenv("GMAIL_USER", os.getenv("PORTAL_USERNAME", "")).strip()
    GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()

    if not GMAIL_USER or not GMAIL_APP_PASSWORD:
        logger.debug("GMAIL_APP_PASSWORD not set in .env. Skipping Gmail IMAP check.")
        return 0

    seen_ids = load_seen_email_ids()
    new_alerts_count = 0

    try:
        mail = imaplib.IMAP4_SSL(GMAIL_IMAP_SERVER)
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("INBOX", readonly=True)

        # Search for emails from MEU sender
        status, messages = mail.search(None, f'(FROM "{MEU_SENDER}")')
        if status != "OK" or not messages or not messages[0]:
            # Fallback search if sender filter varies
            status, messages = mail.search(None, '(SUBJECT "Test Schedule")')

        if status == "OK" and messages and messages[0]:
            email_ids = messages[0].split()
            # Inspect the latest 5 emails from MEU
            for eid in email_ids[-5:]:
                eid_str = eid.decode("utf-8")
                if eid_str in seen_ids:
                    continue

                res, msg_data = mail.fetch(eid, "(RFC822)")
                if res != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                subject = decode_str(msg.get("Subject", ""))
                from_addr = decode_str(msg.get("From", ""))

                # Extract text body using multi-format extractor (Plain + HTML)
                body_text = extract_email_body(msg)

                # Parse quiz schedule
                parsed_data = parse_quiz_schedule_email(subject, body_text)
                if parsed_data:
                    logger.info(f"Detected MEU Quiz Email: '{parsed_data['test_name']}' for {parsed_data['course']}")
                    alert_msg = format_quiz_email_alert(parsed_data)
                    send_telegram_message(alert_msg, parse_mode="HTML")

                    # Arm exact-time quiz sniper
                    try:
                        from src.sniper import quiz_sniper
                        quiz_sniper.arm(parsed_data)
                    except Exception as e_arm:
                        logger.error(f"Failed to arm sniper for {parsed_data.get('test_name')}: {e_arm}")
                    
                    # Cache in state/scheduled_tests.json
                    try:
                        sched_path = os.path.join(BASE_DIR, "state", "scheduled_tests.json")
                        sched_list = []
                        if os.path.exists(sched_path):
                            with open(sched_path, "r", encoding="utf-8") as sf:
                                sched_list = json.load(sf)
                        # Add if not duplicate
                        if not any(st.get("test_name") == parsed_data["test_name"] and st.get("date") == parsed_data["date"] for st in sched_list):
                            sched_list.append(parsed_data)
                            with open(sched_path, "w", encoding="utf-8") as sf:
                                json.dump(sched_list, sf, indent=2)
                    except Exception as ex:
                        logger.warning(f"Could not cache scheduled test: {ex}")

                    save_seen_email_id(eid_str)
                    new_alerts_count += 1
                else:
                    # Mark seen even if generic MEU notification
                    save_seen_email_id(eid_str)

        mail.logout()
    except Exception as e:
        logger.warning(f"Gmail IMAP check notice: {e}")

    return new_alerts_count


class GmailListenerThread:
    """Background worker that periodically checks Gmail every 300 seconds."""

    def __init__(self, interval: int = 300):
        self.interval = interval
        self.running = False
        self.thread: Optional[threading.Thread] = None

    def start(self):
        if not os.getenv("GMAIL_APP_PASSWORD", "").strip():
            logger.info("Gmail App Password not yet configured. Gmail sniper waiting for setup.")
            return

        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="GmailListener")
        self.thread.start()
        logger.info("Gmail Early Alert Listener started in background thread.")

    def stop(self):
        self.running = False

    def _loop(self):
        while self.running:
            try:
                check_gmail_for_meu_alerts()
            except Exception as e:
                logger.debug(f"Gmail loop error: {e}")
            time.sleep(self.interval)


gmail_listener = GmailListenerThread(interval=300)
