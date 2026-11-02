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
    page.goto(LMS_DASHBOARD_URL, wait_until="networkidle", timeout=35000)

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
            p.goto(item["url"], wait_until="networkidle", timeout=25000)
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

    return assignments


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
    """Formats deadline breakdown with countdowns for Telegram."""
    if not assignments:
        return "ℹ️ <b>No active assignments found in LMS.</b>"

    lines = [
        "⏳ <b>UPCOMING ASSIGNMENTS & DEADLINES</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # Sort: unsubmitted first, then by days left
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
