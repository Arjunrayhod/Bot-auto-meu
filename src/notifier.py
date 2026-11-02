"""
Notifier Module: Handles Telegram alerts, updates, and chunked answer delivery.
Complies with Telegram's 4096-character message limits and incorporates rate-limiting delays.
"""

import os
import time
import html
import logging
import requests
from typing import List, Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.notifier")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


def chunk_text(text: str, max_chars: int = 3900) -> List[str]:
    """
    Splits text into chunks of at most `max_chars`, preserving paragraphs/lines
    where possible to prevent splitting code blocks or words awkwardly.
    """
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_len = 0

    for line in lines:
        line_len = len(line) + 1  # include newline
        if line_len > max_chars:
            # If a single line exceeds max_chars, hard-slice it
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_len = 0
            for i in range(0, len(line), max_chars):
                chunks.append(line[i : i + max_chars])
        elif current_len + line_len > max_chars:
            chunks.append("\n".join(current_chunk))
            current_chunk = [line]
            current_len = line_len
        else:
            current_chunk.append(line)
            current_len += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def send_telegram_message(text: str, parse_mode: Optional[str] = None) -> bool:
    """
    Sends a message (or sequential chunks if length > 4000) to Telegram.
    Adds a 1-second delay between chunks to avoid rate limiting.
    """
    token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
    chat_id = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

    if not token or not chat_id:
        logger.warning("Telegram credentials not configured in .env. Skipping alert.")
        print(f"[Notifier Simulated Alert]:\n{text}")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = chunk_text(text, max_chars=3900)
    success_all = True

    for idx, chunk in enumerate(chunks, 1):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "disable_web_page_preview": True,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode

        try:
            resp = requests.post(url, json=payload, timeout=15)
            if not resp.ok:
                # If markdown/HTML parsing failed, retry as plain text
                if parse_mode:
                    payload.pop("parse_mode", None)
                    resp = requests.post(url, json=payload, timeout=15)

                if not resp.ok:
                    logger.error(f"Telegram API error ({resp.status_code}): {resp.text}")
                    success_all = False
            else:
                logger.debug(f"Telegram chunk {idx}/{len(chunks)} sent successfully.")
        except Exception as e:
            logger.error(f"Network error sending Telegram message chunk: {e}")
            success_all = False

        if idx < len(chunks):
            # 1-second delay between sequential chunks to respect Telegram rate limits
            time.sleep(1.0)

    return success_all


def notify_new_task(task_type: str, title: str, task_id: str, extra: Optional[str] = None):
    """Alerts when a new Quiz, Assignment, or Practical is detected."""
    msg = (
        f"🆕 <b>New {task_type.capitalize()} Found</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Title:</b> {title}\n"
        f"🆔 <b>ID:</b> {task_id}\n"
    )
    if extra:
        msg += f"ℹ️ <b>Info:</b> {extra}\n"
    send_telegram_message(msg, parse_mode="HTML")


def notify_server_error(attempt: int, max_retries: int, status_code: int, wait_mins: int):
    """Alerts when portal encounters ASP.NET 500/502/503 server error."""
    msg = (
        f"⚠️ <b>Portal Server Error {status_code}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"Retry attempt: <b>{attempt}/{max_retries}</b>\n"
        f"Pausing for {wait_mins} minutes before re-checking...\n"
    )
    send_telegram_message(msg, parse_mode="HTML")


def notify_quiz_started(subject: str, title: str, task_id: str, time_limit_sec: Optional[int] = None):
    """Alerts when a Quiz solving process begins."""
    limit_str = f"{time_limit_sec // 60}m {time_limit_sec % 60}s" if time_limit_sec else "Auto-detected"
    msg = (
        "🚀 <b>Quiz Solving Started!</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>Subject:</b> {html.escape(subject)}\n"
        f"📝 <b>Quiz:</b> {html.escape(title)}\n"
        f"🆔 <b>Task ID:</b> <code>{html.escape(task_id)}</code>\n"
        f"⏱️ <b>Time Limit:</b> {limit_str}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⚡ <i>Questions extract kiye ja rahe hain aur AI se answers generate ho rahe hain...</i>\n"
        "📲 <i>Kuch hi seconds me complete solutions yahan deliver ho jayenge!</i>"
    )
    send_telegram_message(msg, parse_mode="HTML")


def notify_quiz_solutions(subject: str, title: str, task_id: str, qa_list: List[Dict[str, Any]]):
    """Sends all extracted quiz questions and answers to Telegram."""
    header = (
        "📋 <b>Quiz Questions &amp; Answers</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📚 <b>Subject:</b> {html.escape(subject)}\n"
        f"📝 <b>Quiz:</b> {html.escape(title)}\n"
        f"🆔 <b>ID:</b> <code>{html.escape(task_id)}</code>\n"
        f"🔢 <b>Total Questions:</b> {len(qa_list)}\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
    )

    blocks = []
    for item in qa_list:
        num = item.get("num", 1)
        q = html.escape(item.get("question", "").strip())
        ans = html.escape(item.get("selected_opt", "").strip())
        reason = html.escape(item.get("reason", "").strip()) if item.get("reason") else ""
        block = f"<b>Q{num}.</b> {q}\n👉 <b>Ans:</b> <code>{ans}</code>"
        if reason:
            block += f"\n💡 <i>{reason}</i>"
        blocks.append(block)

    body = "\n\n".join(blocks)
    footer = (
        "\n\n━━━━━━━━━━━━━━━━━━\n"
        "⏱️ <i>Sabhi options portal par tick kar diye gaye hain. 50% safety timer ke sath auto-submit hoga.</i>"
    )

    full_message = header + body + footer
    send_telegram_message(full_message, parse_mode="HTML")


def notify_quiz_completed(title: str, task_id: str, score: Optional[str] = None, elapsed: Optional[str] = None, subject: Optional[str] = None):
    """Alerts when a Quiz is submitted successfully."""
    msg = (
        "✅ <b>Quiz Auto-Submitted Successfully</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )
    if subject:
        msg += f"📚 <b>Subject:</b> {html.escape(subject)}\n"
    msg += (
        f"📝 <b>Quiz:</b> {html.escape(title)}\n"
        f"🆔 <b>ID:</b> <code>{html.escape(task_id)}</code>\n"
    )
    if elapsed:
        msg += f"⏱️ <b>Time Elapsed:</b> {elapsed}\n"
    if score:
        msg += f"🏆 <b>Score:</b> {html.escape(score)}\n"
    send_telegram_message(msg, parse_mode="HTML")


def notify_assignment_solution(title: str, task_id: str, solution_text: str, pdf_name: Optional[str] = None):
    """Sends comprehensive assignment solution to Telegram for manual submission."""
    header = (
        f"📚 <b>Assignment Solution Ready</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Title:</b> {title}\n"
        f"🆔 <b>ID:</b> {task_id}\n"
    )
    if pdf_name:
        header += f"📄 <b>Extracted PDF:</b> {pdf_name}\n"
    header += "━━━━━━━━━━━━━━━━━━\n\n"

    full_message = header + solution_text
    send_telegram_message(full_message)


def notify_practical_solution(title: str, task_id: str, solution_text: str):
    """Sends practical steps/code to Telegram."""
    header = (
        f"🔬 <b>Practical Solution Ready</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📌 <b>Title:</b> {title}\n"
        f"🆔 <b>ID:</b> {task_id}\n"
        f"━━━━━━━━━━━━━━━━━━\n\n"
    )
    full_message = header + solution_text
    send_telegram_message(full_message)


def notify_fatal_alert(message: str):
    """Emergency alert after exhausting retries or encountering unhandled portal states."""
    msg = (
        f"🚨 <b>ACADEMIC BOT CRITICAL ALERT</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{message}\n"
    )
    send_telegram_message(msg, parse_mode="HTML")
