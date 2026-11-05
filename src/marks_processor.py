"""
Marks & MST Processor: Extracts Mid-Semester Test (MST) schedules/marks,
CIA internal assessments, and Semester Exam Results from the MEU GNUMS portal.
Caches data locally for instant (<0.2s) Telegram responses.
"""

import os
import json
import logging
from typing import Dict, Any, List, Optional
from playwright.sync_api import Page

logger = logging.getLogger("academic_bot.marks")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARKS_CACHE_PATH = os.path.join(BASE_DIR, "state", "marks_cache.json")

RESULTS_URL = "https://meu.gnums.co.in/StudentPanel/Exam/Result/EXM_StudentResultDisplay.aspx"
CIA_DETAILS_URL = "https://meu.gnums.co.in/StudentPanel/CIA_InternalAssessment/CIA_InternalAssessmentStudentDetails.aspx"
CIA_SCHEDULE_URL = "https://meu.gnums.co.in/StudentPanel/CIA_InternalAssessment/CIA_InternalAssessmentScheduleList_For_Student.aspx"


def extract_marks_data(page: Page) -> Dict[str, Any]:
    """
    Scrapes Exam Results, CIA details, and MST Schedules from the portal.
    """
    marks_data: Dict[str, Any] = {
        "mst_assessments": [],
        "cia_details": [],
        "exam_results": {},
        "updated_at": "",
    }

    import datetime
    marks_data["updated_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 1. Scrape Exam Results
    try:
        logger.info(f"Navigating to Exam Results: {RESULTS_URL}")
        page.goto(RESULTS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        ddl = page.locator("#ctl00_cphPageContent_ddlExamID")
        if ddl.count() > 0:
            options = [
                opt.inner_text().strip()
                for opt in ddl.locator("option").all()
                if opt.inner_text().strip() and "Select" not in opt.inner_text()
            ]

            for exam_name in options:
                try:
                    logger.info(f"Selecting exam: {exam_name}")
                    ddl.select_option(label=exam_name)
                    page.wait_for_timeout(2500)

                    # Check for Show button if needed
                    show_btn = page.locator("input[value*='Show'], button:has-text('Show'), input[id*='btnShow']")
                    if show_btn.count() > 0 and show_btn.first.is_visible():
                        show_btn.first.click()
                        page.wait_for_timeout(2500)

                    table = page.locator("table").first
                    if table.count() > 0:
                        rows = table.locator("tr").all()
                        course_list = []
                        for row in rows[1:]:  # skip header
                            cols = [c.inner_text().strip() for c in row.locator("td").all()]
                            if len(cols) >= 5:
                                course_list.append({
                                    "sr": cols[0],
                                    "code": cols[1],
                                    "course": cols[2],
                                    "credit": cols[3],
                                    "grade": cols[4],
                                })
                        marks_data["exam_results"][exam_name] = course_list
                except Exception as ex:
                    logger.warning(f"Error scraping exam '{exam_name}': {ex}")

    except Exception as e:
        logger.error(f"Error accessing Exam Results: {e}")

    # 2. Scrape CIA Student Details
    try:
        logger.info(f"Navigating to CIA Details: {CIA_DETAILS_URL}")
        page.goto(CIA_DETAILS_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        cia_tables = page.locator("table").all()
        for tbl in cia_tables:
            rows = tbl.locator("tr").all()
            for r in rows:
                cols = [c.inner_text().strip() for c in r.locator("td, th").all()]
                if cols and len(cols) > 1:
                    marks_data["cia_details"].append(" | ".join(cols))

    except Exception as e:
        logger.error(f"Error accessing CIA Details: {e}")

    # 3. Scrape Mid Exam / CIA Schedule
    try:
        logger.info(f"Navigating to CIA Schedule: {CIA_SCHEDULE_URL}")
        page.goto(CIA_SCHEDULE_URL, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2000)

        sched_select = page.locator("select").first
        if sched_select.count() > 0:
            opts = [
                o.inner_text().strip()
                for o in sched_select.locator("option").all()
                if o.inner_text().strip() and "Select" not in o.inner_text()
            ]
            marks_data["mst_assessments"] = opts

    except Exception as e:
        logger.error(f"Error accessing CIA Schedule: {e}")

    # Save to local cache
    save_cached_marks(marks_data)
    return marks_data


def save_cached_marks(data: Dict[str, Any]):
    """Persists extracted marks data to state/marks_cache.json."""
    try:
        os.makedirs(os.path.dirname(MARKS_CACHE_PATH), exist_ok=True)
        with open(MARKS_CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info(f"Marks data cached to: {MARKS_CACHE_PATH}")
    except Exception as e:
        logger.error(f"Failed to cache marks data: {e}")


def get_cached_marks() -> Optional[Dict[str, Any]]:
    """Reads marks cache from disk if available."""
    if os.path.exists(MARKS_CACHE_PATH):
        try:
            with open(MARKS_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error reading marks cache: {e}")
    return None


def format_marks_report(data: Dict[str, Any]) -> str:
    """
    Formats marks, MST status, and exam results into a clean, rich HTML report.
    """
    lines = [
        "🎯 <b>TEST MARKS &amp; MST ASSESSMENT REPORT</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    # Section 1: Mid Semester Tests (MST) Status
    lines.append("📝 <b>Mid Semester Test (MST) Status:</b>")
    mst_list = data.get("mst_assessments", [])
    if mst_list:
        for mst in mst_list:
            clean_mst = mst.replace(" | ", " • ")
            lines.append(f"• <b>{clean_mst}</b>")
        lines.append("ℹ️ <i>Faculty marks entry in progress for current term. You will be alerted the moment marks are entered!</i>")
    else:
        lines.append("• <i>No active MST records currently published.</i>")

    lines.append("━━━━━━━━━━━━━━━━━━")

    # Section 2: Semester Exam Results & Subject Grades
    exam_results = data.get("exam_results", {})
    if exam_results:
        for exam_title, courses in exam_results.items():
            lines.append(f"🎓 <b>{exam_title}</b>")
            for c in courses:
                grade = c.get("grade", "-")
                course_name = c.get("course", "")
                credits = c.get("credit", "")

                # Grade icon
                if grade in ("A+", "A"):
                    g_icon = "🟢"
                elif grade in ("B+", "B"):
                    g_icon = "🟡"
                elif grade in ("C", "P"):
                    g_icon = "🟠"
                else:
                    g_icon = "⚪"

                lines.append(f"{g_icon} <b>{course_name}</b>: <code>{grade}</code> ({credits} Cr)")
            lines.append("")
    else:
        lines.append("ℹ️ <i>Official semester results not yet available.</i>\n")

    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"🕒 <i>Last synced: {data.get('updated_at', 'Just now')}</i>")
    return "\n".join(lines)


def format_declared_results_report(tests: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Formats all declared online quiz and test marks/scores with release dates into an HTML report.
    """
    if tests is None:
        try:
            from src.deadline_tracker import get_cached_tests
            tests = get_cached_tests() or []
        except Exception:
            tests = []
    lines = [
        "🏆 <b>DECLARED QUIZ & TEST RESULTS (SCORES)</b>",
        "━━━━━━━━━━━━━━━━━━",
        "Portal par jin-jin quizzes aur tests ke numbers declare ho chuke hain:",
        "",
    ]

    passed_tests = []
    other_tests = []
    pending_tests = []

    for t in tests:
        marks = t.get("marks", "-").strip()
        result = t.get("result", "").strip()
        name = t.get("test_name", "Quiz")
        subj = t.get("subject", "Academic Course")
        date = t.get("test_date", "N/A")

        if "will be display" in result.lower() or marks == "-":
            pending_tests.append((name, subj, result, date))
        elif marks != "-":
            if "pass" in result.lower():
                passed_tests.append((name, subj, marks, result, date))
            else:
                other_tests.append((name, subj, marks, result or "Evaluated", date))

    # 1. Passed & High-Scoring Quizzes
    if passed_tests:
        lines.append("🟢 <b>PASSED QUIZZES & SCORES:</b>")
        for name, subj, marks, res, date in passed_tests:
            star = " ⭐ <b>(Full Marks!)</b>" if ("15.00 / 15" in marks or "20.00 / 20" in marks) else ""
            lines.append(f"• <b>{name}</b> — <i>{subj}</i>")
            lines.append(f"   ├ 📊 <b>Score:</b> <code>{marks}</code>{star}")
            lines.append(f"   ├ 🟢 <b>Result:</b> <b>{res}</b>")
            lines.append(f"   └ 📅 <b>Date:</b> {date}")
            lines.append("")
    else:
        lines.append("ℹ️ <i>Koi passed quiz record abhi nahi mila.</i>")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━")

    # 2. Other evaluated / Not attempted
    if other_tests:
        lines.append("📋 <b>OTHER EVALUATIONS:</b>")
        for name, subj, marks, res, date in other_tests:
            icon = "🔴" if "fail" in res.lower() else "⚪"
            lines.append(f"• <b>{name}</b> — <i>{subj}</i>")
            lines.append(f"   ├ 📊 <b>Score:</b> <code>{marks}</code>")
            lines.append(f"   ├ {icon} <b>Status:</b> {res}")
            lines.append(f"   └ 📅 <b>Date:</b> {date}")
            lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━")

    # 3. Results releasing soon
    if pending_tests:
        lines.append("⏳ <b>RESULTS RELEASING SOON:</b>")
        seen_pending = set()
        for name, subj, res_text, date in pending_tests:
            key = (name, subj)
            if key not in seen_pending:
                seen_pending.add(key)
                lines.append(f"• <b>{name}</b> (<i>{subj}</i>)")
                lines.append(f"   └ 🕒 <i>{res_text}</i>")
                lines.append("")
        lines.append("━━━━━━━━━━━━━━━━━━")

    # 4. Note on Assignments
    lines.append("📚 <b>Assignments Evaluation:</b>")
    lines.append("• Sabhi assignments ke solutions chat me send ho chuke hain.")
    lines.append("• Portal par internal faculty grading update hote hi yahan number show ho jayenge.")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append("💡 <i>Tip: Jaise hi portal par naya result update hota hai, bot automatic yahan reflect kar deta hai!</i>")

    return chr(10).join(lines)
