"""
Deadline Tracker: Scans LMS assignments for approaching due dates and unsubmitted tasks.
Issues early warning alerts so students never miss an assignment deadline.
"""

import os
import re
import json
import logging
from datetime import datetime, date
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from playwright.sync_api import Page
from urllib.parse import urljoin

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.deadlines")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEADLINES_CACHE_PATH = os.path.join(BASE_DIR, "state", "deadlines_cache.json")
TESTS_CACHE_PATH = os.path.join(BASE_DIR, "state", "tests_cache.json")
LMS_DASHBOARD_URL = "https://meu.gnums.co.in/StudentPanel/LMS/LMS_ContentStudentDashboard.aspx"


def parse_portal_date(date_str: str) -> Optional[date]:
    """Parses various date formats seen on portal, e.g. '16-11-2026', '16/11/2026', '2026-11-16'."""
    clean = date_str.strip()
    for fmt in ("%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%b-%Y"):
        try:
            return datetime.strptime(clean, fmt).date()
        except ValueError:
            continue
    return None


def extract_assignments_deadlines(page: Page) -> List[Dict[str, Any]]:
    """
    Scrapes the LMS dashboard and individual assignment links to build a deadline ledger.
    """
    logger.info(f"Navigating to LMS Dashboard: {LMS_DASHBOARD_URL}")
    page.goto(LMS_DASHBOARD_URL, wait_until="domcontentloaded", timeout=45000)
    try:
        page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:
        pass

    assignments = []
    today = date.today()

    # Find assignment links
    links = page.locator("a[href*='LMS_Content_ViewDetailedForStudent']")
    link_count = links.count()
    logger.info(f"Discovered {link_count} detailed assignment links.")

    collected_urls = set()
    for i in range(link_count):
        l = links.nth(i)
        href = l.get_attribute("href") or ""
        txt = l.inner_text().strip()
        full_url = urljoin(page.url, href)

        if full_url in collected_urls:
            continue
        collected_urls.add(full_url)

        assignments.append({
            "title": txt or f"Assignment #{i+1}",
            "url": full_url,
            "due_date": None,
            "days_left": None,
            "is_submitted": False,
        })

    # Sample top 6 most relevant/recent assignments to inspect due dates
    context = page.context
    for item in assignments[:8]:
        try:
            p = context.new_page()
            p.goto(item["url"], wait_until="domcontentloaded", timeout=35000)
            b_text = p.inner_text("body")

            # Check submission status
            item["is_submitted"] = "Not submitted" not in b_text and "Submitted" in b_text

            # Check due date: "Due date : 16-11-2026"
            m_due = re.search(r"Due\s*date\s*:\s*(\d{1,2}[-/]\d{1,2}[-/]\d{4})", b_text, re.IGNORECASE)
            if m_due:
                d_str = m_due.group(1)
                item["due_date_str"] = d_str
                p_date = parse_portal_date(d_str)
                if p_date:
                    item["due_date"] = str(p_date)
                    delta = (p_date - today).days
                    item["days_left"] = delta

            # Subject / Unit
            m_unit = re.search(r"Unit\s*:\s*([^\n]+)", b_text)
            if m_unit:
                item["unit"] = m_unit.group(1).strip()

            p.close()
        except Exception as e:
            logger.debug(f"Error inspecting assignment {item['title']}: {e}")

    # Save to cache
    os.makedirs(os.path.dirname(DEADLINES_CACHE_PATH), exist_ok=True)
    try:
        with open(DEADLINES_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(assignments, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to cache deadlines: {e}")

    # Also extract and cache online tests/quizzes from LMS TEST LIST
    try:
        extract_lms_tests(page)
    except Exception as e:
        logger.warning(f"Error scraping LMS tests during deadline cycle: {e}")

    return assignments


def extract_lms_tests(page: Page) -> List[Dict[str, Any]]:
    """
    Scrapes the LMS TEST LIST table for online quizzes and assessments.
    Extracts Date, Time Window, Duration, Subject, Test Name, Marks, and Attemptability.
    """
    tests = []
    try:
        if "LMS_ContentStudentDashboard.aspx" not in page.url:
            page.goto(LMS_DASHBOARD_URL, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

        rows = page.locator("table tr")
        row_count = rows.count()
        for i in range(row_count):
            row = rows.nth(i)
            r_text = row.inner_text().strip()
            if not r_text or "Sr." in r_text:
                continue

            if any(k in r_text.lower() for k in ["min.", "mcq", "quiz", "test duration", "test is submitted", "test date"]):
                cols = [c.inner_text().strip() for c in row.locator("td").all()]
                if len(cols) >= 6:
                    date_col = cols[1]
                    m_date = re.search(r"(\d{1,2}-\d{1,2}-\d{4})", date_col)
                    test_date = m_date.group(1) if m_date else ""

                    time_col = cols[2]
                    m_dur = re.search(r"(\d+)\s*Min\.", time_col, re.IGNORECASE)
                    duration_str = f"{m_dur.group(1)} Min." if m_dur else ""

                    m_time = re.search(r"(\d{1,2}:\d{2}\s*(?:AM|PM)\s*to\s*\d{1,2}:\d{2}\s*(?:AM|PM))", time_col, re.IGNORECASE)
                    time_window = m_time.group(1) if m_time else time_col.split("\n")[0].strip()

                    subject = cols[3]
                    test_name = cols[4]
                    re_attempt_date = cols[5] if len(cols) > 5 else ""
                    test_type = cols[7] if len(cols) > 7 else "MCQ"
                    questions_count = cols[8] if len(cols) > 8 else ""
                    marks = cols[9] if len(cols) > 9 else "-"
                    result_status = cols[11] if len(cols) > 11 else ""
                    action_status = cols[12] if len(cols) > 12 else ""

                    action_btn = row.locator("a, button, input[type='submit']")
                    action_text = ""
                    action_href = ""
                    is_active_now = False
                    if action_btn.count() > 0:
                        action_text = action_btn.first.inner_text().strip()
                        action_href = action_btn.first.get_attribute("href") or ""
                        is_active_now = (any(w in action_text.lower() for w in ["start", "attempt", "click here"]) or "start" in action_href.lower()) and "not attempted" not in action_text.lower() and "over" not in r_text.lower()
                    is_submitted = "submitted" in r_text.lower()
                    is_over = "duration is over" in r_text.lower() or "over" in r_text.lower()

                    tests.append({
                        "sr": cols[0],
                        "test_date": test_date,
                        "time_window": time_window,
                        "duration": duration_str,
                        "subject": subject,
                        "test_name": test_name,
                        "re_attempt_date": re_attempt_date,
                        "type": test_type,
                        "questions": questions_count,
                        "marks": marks,
                        "result": result_status,
                        "action_status": action_status,
                        "can_attempt": is_active_now,
                        "is_submitted": is_submitted,
                        "is_over": is_over,
                        "action_text": action_text,
                        "action_href": action_href,
                    })

        os.makedirs(os.path.dirname(TESTS_CACHE_PATH), exist_ok=True)
        with open(TESTS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(tests, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Error scraping LMS TEST LIST: {e}")

    return tests


def get_cached_tests() -> List[Dict[str, Any]]:
    """Retrieves cached online test and quiz list."""
    if os.path.exists(TESTS_CACHE_PATH):
        try:
            with open(TESTS_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def format_tests_report(tests: List[Dict[str, Any]]) -> str:
    """Formats online tests & quiz breakdown with timing and status for Telegram."""
    if not tests:
        return "ℹ️ <b>No tests or quizzes currently recorded in LMS.</b>"

    lines = [
        "📝 <b>ONLINE QUIZZES & TESTS (LMS SCHEDULE)</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    for t in tests[:8]:
        subj = t.get("subject", "Course")
        name = t.get("test_name", "Quiz")
        t_date = t.get("test_date", "N/A")
        t_time = t.get("time_window", "N/A")
        dur = t.get("duration", "N/A")
        marks = t.get("marks", "-")

        if t.get("can_attempt"):
            badge = "🟢 <b>ACTIVE NOW (Ready to Solve)!</b>"
        elif t.get("is_submitted"):
            badge = f"✅ <b>Submitted</b> (Score: {marks})"
        elif t.get("is_over"):
            badge = "⚠️ <b>Duration Over</b>"
        else:
            badge = f"ℹ️ {t.get('action_status') or 'Scheduled'}"

        lines.append(f"🎯 <b>{name}</b> — <i>{subj}</i>")
        lines.append(f"   ├ 📅 <b>Date:</b> <code>{t_date}</code>")
        lines.append(f"   ├ ⏰ <b>Time Window:</b> <code>{t_time}</code>")
        lines.append(f"   ├ ⏱️ <b>Duration:</b> <code>{dur}</code>")
        lines.append(f"   └ 📊 <b>Status:</b> {badge}\n")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ <i>Bot checks for active quiz windows every cycle and auto-solves via Gemini AI.</i>")
    return "\n".join(lines)


def get_cached_deadlines() -> List[Dict[str, Any]]:
    """Retrieves cached assignment deadline list."""
    if os.path.exists(DEADLINES_CACHE_PATH):
        try:
            with open(DEADLINES_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []


def format_deadlines_report(assignments: List[Dict[str, Any]]) -> str:
    """Formats deadline breakdown with countdowns for Telegram, including online tests if available."""
    cached_tests = get_cached_tests()

    lines = []

    # If recent or active tests exist, show them on top!
    if cached_tests:
        lines.append("📝 <b>UPCOMING / RECENT ONLINE TESTS:</b>")
        for t in cached_tests[:4]:
            t_name = t.get("test_name", "Quiz")
            t_subj = t.get("subject", "").split("-")[-1].strip() or t.get("subject", "")
            t_date = t.get("test_date", "N/A")
            t_time = t.get("time_window", "N/A")
            dur = t.get("duration", "N/A")
            status = "🟢 ACTIVE" if t.get("can_attempt") else ("✅ Submitted" if t.get("is_submitted") else "⚠️ Ended")
            lines.append(f"• <b>{t_name}</b> ({t_subj})")
            lines.append(f"  📅 <code>{t_date}</code> | ⏰ <code>{t_time}</code> ({dur}) | {status}")
        lines.append("━━━━━━━━━━━━━━━━━━")

    lines.append("⏳ <b>UPCOMING ASSIGNMENTS & DEADLINES:</b>")
    lines.append("━━━━━━━━━━━━━━━━━━")

    if not assignments:
        lines.append("ℹ️ <i>No active assignments found in LMS.</i>")
    else:
        sorted_items = sorted(
            assignments,
            key=lambda x: (x.get("is_submitted", False), x.get("days_left") if x.get("days_left") is not None else 999),
        )

        for a in sorted_items:
            title = a.get("title", "Assignment")
            sub_status = "✅ <b>Submitted</b>" if a.get("is_submitted") else "❌ <b>Not Submitted</b>"
            days = a.get("days_left")
            due_str = a.get("due_date_str", "N/A")

            if days is not None:
                if days < 0:
                    time_badge = "⚠️ <i>(Deadline Passed)</i>"
                elif days == 0:
                    time_badge = "🔥 <b>(Due TODAY!)</b>"
                elif days == 1:
                    time_badge = "🚨 <b>(Due Tomorrow!)</b>"
                elif days <= 3:
                    time_badge = f"⏰ <b>(Due in {days} days)</b>"
                else:
                    time_badge = f"📅 ({days} days left)"
            else:
                time_badge = f"📅 Due: {due_str}"

            lines.append(f"📌 <b>{title}</b>")
            lines.append(f"   ├ Status: {sub_status}")
            lines.append(f"   └ Deadline: <code>{due_str}</code> {time_badge}\n")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Note: Solutions for all assignments have already been generated and sent to this chat!</i>")

    return "\n".join(lines)


def get_urgent_deadline_alert(assignments: List[Dict[str, Any]], days_threshold: int = 2) -> Optional[str]:
    """Returns alert if unsubmitted assignment is due within threshold days."""
    urgent = []
    for a in assignments:
        if not a.get("is_submitted", False):
            days = a.get("days_left")
            if days is not None and 0 <= days <= days_threshold:
                urgent.append(a)

    if not urgent:
        return None

    lines = [
        "🚨 <b>URGENT ASSIGNMENT DEADLINE WARNING!</b>",
        "━━━━━━━━━━━━━━━━━━",
        "The following assignments must be submitted immediately:",
        "",
    ]
    for u in urgent:
        days = u.get("days_left", 0)
        due_badge = "TODAY!" if days == 0 else ("TOMORROW!" if days == 1 else f"in {days} days!")
        lines.append(f"🔥 <b>{u.get('title')}</b>")
        lines.append(f"   Due: <b>{u.get('due_date_str')} ({due_badge})</b>\n")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("👉 Check previous messages in this chat for the complete AI-generated solution.")

    return "\n".join(lines)
