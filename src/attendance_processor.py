"""
Attendance Processor: Scrapes subject-wise attendance from Mandsaur University portal.
Detects low attendance (<75%), calculates recovery classes required, and formats Telegram reports.
"""

import os
import re
import json
import math
import logging
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv
from playwright.sync_api import Page

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.attendance")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ATTENDANCE_CACHE_PATH = os.path.join(BASE_DIR, "state", "attendance_cache.json")
ATTENDANCE_URL = "https://meu.gnums.co.in/StudentPanel/TTM_Attendance/TTM_Attendance_StudentAttendance.aspx"
LOW_ATTENDANCE_THRESHOLD = 75.0


def calculate_recovery_classes(conducted: int, present: int, target_pct: float = 75.0) -> int:
    """
    Calculates minimum consecutive classes student must attend to reach target percentage:
    (present + x) / (conducted + x) >= target_pct / 100
    x >= (target * conducted - 100 * present) / (100 - target)
    """
    current_pct = (present / conducted * 100.0) if conducted > 0 else 100.0
    if current_pct >= target_pct:
        return 0

    numerator = (target_pct * conducted) - (100.0 * present)
    denominator = 100.0 - target_pct
    return max(0, math.ceil(numerator / denominator))


def extract_attendance_data(page: Page) -> Dict[str, Any]:
    """
    Scrapes the Attendance Summary and Subject-wise table (#tblAttendance).
    """
    logger.info(f"Navigating to Attendance page: {ATTENDANCE_URL}")
    page.goto(ATTENDANCE_URL, wait_until="networkidle", timeout=35000)

    # 1. Overall stats
    body_text = page.inner_text("body")

    overall_pct = 0.0
    # Match patterns like: "82.02 %" or "Present %\n82.02"
    m_pct = re.search(r"(\d{1,3}(?:\.\d{1,2})?)\s*%\s*\n*Present %", body_text)
    if not m_pct:
        m_pct = re.search(r"Semester\s*-\s*3[^\n]*\n+(\d{1,3}(?:\.\d{1,2})?)\s*%", body_text)
    if m_pct:
        try:
            overall_pct = float(m_pct.group(1))
        except ValueError:
            pass

    # Extract slots summary
    total_slots = 0
    present_slots = 0
    absent_slots = 0

    m_conducted = re.search(r"(\d+)\s*\n*Total Slots", body_text)
    if m_conducted:
        total_slots = int(m_conducted.group(1))

    m_present = re.search(r"(\d+)\s*\n*Present Slots", body_text)
    if m_present:
        present_slots = int(m_present.group(1))

    m_absent = re.search(r"(\d+)\s*\n*Absent Slots", body_text)
    if m_absent:
        absent_slots = int(m_absent.group(1))

    # 2. Extract subject-wise rows from #tblAttendance
    subjects = []
    rows = page.locator("table#tblAttendance tbody tr, table.dataTable tbody tr, table tr:has(td)")
    row_count = rows.count()

    for i in range(row_count):
        row = rows.nth(i)
        tds = row.locator("td")
        if tds.count() < 6:
            continue

        try:
            slot_type = tds.nth(1).inner_text().strip()
            course_raw = tds.nth(2).inner_text().strip().replace("\n", " ")
            conducted = int(re.sub(r"\D", "", tds.nth(3).inner_text().strip() or "0"))
            present = int(re.sub(r"\D", "", tds.nth(4).inner_text().strip() or "0"))
            absent = int(re.sub(r"\D", "", tds.nth(5).inner_text().strip() or "0"))

            pct_text = tds.nth(6).inner_text().strip()
            pct_match = re.search(r"(\d+(?:\.\d+)?)", pct_text)
            pct = float(pct_match.group(1)) if pct_match else (round(present / conducted * 100, 2) if conducted else 100.0)

            # Clean Course Name (e.g. 25BCC080T - Fundamentals of Storage)
            course_name = course_raw
            if " - " in course_raw:
                parts = course_raw.split(" - ", 1)
                code = parts[0].strip()
                name = parts[1].strip()
            else:
                code = ""
                name = course_raw

            recovery = calculate_recovery_classes(conducted, present, LOW_ATTENDANCE_THRESHOLD)

            subjects.append({
                "slot_type": slot_type,
                "course_code": code,
                "course_name": name,
                "conducted": conducted,
                "present": present,
                "absent": absent,
                "percentage": pct,
                "is_low": pct < LOW_ATTENDANCE_THRESHOLD,
                "recovery_needed": recovery,
            })
        except Exception as e:
            logger.debug(f"Row {i} parse skipped: {e}")
            continue

    # Calculate overall if not found
    if overall_pct == 0.0 and total_slots > 0:
        overall_pct = round((present_slots / total_slots) * 100.0, 2)

    result = {
        "overall_percentage": overall_pct,
        "total_slots": total_slots,
        "present_slots": present_slots,
        "absent_slots": absent_slots,
        "subjects": subjects,
    }

    # Save to cache
    os.makedirs(os.path.dirname(ATTENDANCE_CACHE_PATH), exist_ok=True)
    try:
        with open(ATTENDANCE_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"Failed to cache attendance: {e}")

    return result


def get_cached_attendance() -> Optional[Dict[str, Any]]:
    """Loads latest cached attendance without re-scraping if available."""
    if os.path.exists(ATTENDANCE_CACHE_PATH):
        try:
            with open(ATTENDANCE_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None
    return None


def format_attendance_report(data: Dict[str, Any]) -> str:
    """Formats full attendance breakdown for Telegram display."""
    overall = data.get("overall_percentage", 0.0)
    total = data.get("total_slots", 0)
    present = data.get("present_slots", 0)
    absent = data.get("absent_slots", 0)

    status_icon = "🟢" if overall >= 75.0 else "🔴"

    lines = [
        f"📊 <b>ATTENDANCE REPORT (Semester 3)</b>",
        f"━━━━━━━━━━━━━━━━━━",
        f"{status_icon} <b>Overall Attendance:</b> <code>{overall:.2f}%</code>",
        f"✅ <b>Present Slots:</b> {present} / {total}",
        f"❌ <b>Absent Slots:</b> {absent}",
        f"━━━━━━━━━━━━━━━━━━",
        f"📚 <b>Subject-wise Breakdown:</b>\n",
    ]

    for s in data.get("subjects", []):
        pct = s["percentage"]
        icon = "🟢" if pct >= 75.0 else "⚠️"
        c_name = s["course_name"][:35]
        slot = f"[{s['slot_type'][:3]}]"

        lines.append(f"{icon} <b>{c_name}</b> {slot}")
        lines.append(
            f"   ├ %: <b>{pct:.1f}%</b> (P: {s['present']} | A: {s['absent']} | Total: {s['conducted']})"
        )
        if s["is_low"]:
            lines.append(f"   └ 🚨 <i>Attend next <b>{s['recovery_needed']}</b> classes continuously to reach 75%!</i>")
        lines.append("")

    return "\n".join(lines)


def format_low_attendance_alert(data: Dict[str, Any]) -> Optional[str]:
    """Generates an urgent warning message if any subject is under 75%."""
    low_subs = [s for s in data.get("subjects", []) if s.get("is_low")]
    if not low_subs:
        return None

    lines = [
        "🚨 <b>LOW ATTENDANCE WARNING (&lt;75%)</b>",
        "━━━━━━━━━━━━━━━━━━",
        "Following subjects require immediate attention:",
        "",
    ]

    for s in low_subs:
        lines.append(f"⚠️ <b>{s['course_name']}</b> ({s['slot_type']})")
        lines.append(f"   • Current: <b>{s['percentage']:.1f}%</b> (Absent: {s['absent']})")
        lines.append(f"   • Target Needed: <b>Attend next {s['recovery_needed']} classes</b> to touch 75%!\n")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Tip: Do not miss upcoming lectures in these subjects to avoid exam debarment.</i>")

    return "\n".join(lines)
