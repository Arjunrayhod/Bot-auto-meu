"""
Monitor Module: Scans the Student Dashboard for Quizzes, Assignments, and Practicals.
Tracks seen tasks in state/seen_tasks.json and delegates new tasks to respective processors.
"""

import os
import re
import json
import time
import hashlib
import logging
from typing import Dict, Any, List, Optional
from urllib.parse import urljoin
from dotenv import load_dotenv
from playwright.sync_api import Page

from src.notifier import notify_new_task, notify_fatal_alert
from src.quiz_processor import process_quiz
from src.assignment_processor import process_assignment
from src.practical_processor import process_practical

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.monitor")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEEN_TASKS_PATH = os.path.join(BASE_DIR, "state", "seen_tasks.json")
DASHBOARD_URL = os.getenv("PORTAL_DASHBOARD_URL", "https://meu.gnums.co.in/StudentPanel/StudentDashboard.aspx")


class TaskLedger:
    """Manages persistent tracking of detected tasks to prevent duplicate processing."""

    def __init__(self, filepath: str = SEEN_TASKS_PATH):
        self.filepath = filepath
        self.data: Dict[str, Dict[str, Any]] = {
            "quizzes": {},
            "assignments": {},
            "practicals": {},
        }
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"Error reading seen_tasks.json: {e}. Reinitializing.")
                self.data = {"quizzes": {}, "assignments": {}, "practicals": {}}
        else:
            self.save()

    def save(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Error saving seen_tasks.json: {e}")

    def is_seen(self, task_type: str, task_id: str) -> bool:
        category = self.data.get(task_type, {})
        return task_id in category

    def mark_seen(self, task_type: str, task_id: str, title: str, status: str = "processed", metadata: Optional[dict] = None):
        if task_type not in self.data:
            self.data[task_type] = {}
        self.data[task_type][task_id] = {
            "title": title,
            "status": status,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "metadata": metadata or {},
        }
        self.save()


ledger = TaskLedger()


def _generate_task_id(title: str, task_type: str, extra: str = "") -> str:
    """Generates a stable identifier if the portal does not supply an explicit query param ID."""
    raw = f"{task_type}:{title}:{extra}".strip().lower()
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def detect_task_category(text: str) -> Optional[str]:
    """Classifies a task into 'quizzes', 'assignments', or 'practicals'."""
    lower = text.lower()
    if any(k in lower for k in ["quiz", "test", "exam", "assessment", "mcq"]):
        return "quizzes"
    if any(k in lower for k in ["assignment", "homework", "task", "project"]):
        return "assignments"
    if any(k in lower for k in ["practical", "lab", "experiment", "journal"]):
        return "practicals"
    return None


def extract_tasks_from_page(page: Page) -> List[Dict[str, Any]]:
    """
    Scrapes tables, cards, and gridviews on the current page for actionable tasks.
    Extracts title, link/onclick, task ID, and category.
    """
    detected_tasks = []

    # 1. Inspect Table Rows in GridViews (Standard ASP.NET pattern)
    rows = page.locator("table tr:has(a), table[id*='gv'] tr, table[id*='Grid'] tr")
    row_count = rows.count()

    for i in range(row_count):
        row = rows.nth(i)
        # Skip header rows
        if row.locator("th").count() > 0:
            continue

        row_text = row.inner_text().strip()
        category = detect_task_category(row_text)
        if not category:
            continue

        # Find action link (e.g., 'Attempt', 'Start', 'View', 'Submit', title link)
        links = row.locator("a")
        if links.count() == 0:
            continue

        # Best action link
        action_link = links.first
        for j in range(links.count()):
            l = links.nth(j)
            txt = l.inner_text().lower()
            if any(w in txt for w in ["attempt", "start", "view", "open", "details"]):
                action_link = l
                break

        title = links.first.inner_text().strip() or f"Task #{i}"
        href = action_link.get_attribute("href") or ""

        # Extract explicit ID from href or __doPostBack or row text
        task_id = ""
        id_match = re.search(r"[?&](?:id|taskid|quizid|assignid|id)=(\w+)", href, re.IGNORECASE)
        if id_match:
            task_id = id_match.group(1)
        elif "javascript:__doPostBack" in href:
            postback_match = re.search(r"__doPostBack\('([^']+)'", href)
            if postback_match:
                task_id = postback_match.group(1).replace("$", "_")

        if not task_id:
            task_id = _generate_task_id(title, category, row_text[:50])

        detected_tasks.append({
            "task_id": task_id,
            "title": title,
            "category": category,
            "href": href,
            "locator": action_link,
            "row_info": row_text.replace("\n", " | ")[:120],
        })

    # 2. Inspect Dashboard Cards or Panels
    cards = page.locator(".card, .panel, .dashboard-item")
    for i in range(cards.count()):
        card = cards.nth(i)
        c_text = card.inner_text().strip()
        category = detect_task_category(c_text)
        if not category:
            continue

        links = card.locator("a")
        if links.count() > 0:
            action_link = links.first
            title = links.first.inner_text().strip() or f"Card Item {i}"
            href = action_link.get_attribute("href") or ""
            task_id = _generate_task_id(title, category)

            # Avoid duplicates if already collected via table rows
            if not any(t["task_id"] == task_id for t in detected_tasks):
                detected_tasks.append({
                    "task_id": task_id,
                    "title": title,
                    "category": category,
                    "href": href,
                    "locator": action_link,
                    "row_info": c_text.replace("\n", " ")[:100],
                })

    # 3. Inspect Notice Board / Notifications (#tab_Notification, #tab_LMSNotification)
    notif_links = page.locator("#tab_Notification a, #tab_LMSNotification a, a[href*='STU_Notification']")
    for n_idx in range(notif_links.count()):
        nl = notif_links.nth(n_idx)
        n_txt = nl.inner_text().strip()
        n_href = nl.get_attribute("href") or ""
        if n_txt and not n_href.startswith("#"):
            task_id = _generate_task_id(n_txt, "notifications", n_href[:40])
            if not any(t["task_id"] == task_id for t in detected_tasks):
                detected_tasks.append({
                    "task_id": task_id,
                    "title": n_txt,
                    "category": "notifications",
                    "href": n_href,
                    "locator": nl,
                    "row_info": f"Notice: {n_txt}",
                })

    return detected_tasks


def scan_dashboard_and_process_tasks(page: Page) -> int:
    """
    Navigates to Dashboard, discovers new tasks across Quizzes, Assignments, and Practicals,
    alerts Telegram, and launches the appropriate autonomous processor.
    Returns the count of newly processed tasks.
    """
    logger.info(f"Navigating to Dashboard: {DASHBOARD_URL}")
    page.goto(DASHBOARD_URL, wait_until="networkidle", timeout=30000)

    # 1. Discover tasks on main dashboard
    tasks = extract_tasks_from_page(page)
    logger.info(f"Dashboard scan detected {len(tasks)} candidate task items.")

    new_task_count = 0

    for task in tasks:
        task_id = task["task_id"]
        category = task["category"]
        title = task["title"]

        if ledger.is_seen(category, task_id):
            logger.debug(f"Skipping already-seen {category} task: {title} (ID: {task_id})")
            continue

        new_task_count += 1
        logger.info(f"New task found: [{category.upper()}] '{title}' (ID: {task_id})")

        # Telegram Alert
        notify_new_task(
            task_type=category.rstrip("s"),
            title=title,
            task_id=task_id,
            extra=task.get("row_info"),
        )

        # Open task page (new tab or same page)
        try:
            logger.info(f"Opening task page for {title}...")
            # We open task in a new page context to keep dashboard session clean
            context = page.context
            task_page = context.new_page()

            href = task.get("href", "")
            if href and not href.startswith("javascript"):
                full_url = urljoin(page.url, href)
                task_page.goto(full_url, wait_until="networkidle", timeout=35000)
            else:
                # Click the action link directly in main page or clone navigation
                task["locator"].click()
                page.wait_for_timeout(2000)
                task_page = page

            # Route to respective processor
            success = False
            if category == "quizzes":
                success = process_quiz(task_page, task_id, title)
            elif category == "assignments":
                success = process_assignment(task_page, task_id, title)
            elif category == "practicals":
                success = process_practical(task_page, task_id, title)
            elif category == "notifications":
                success = True

            if success:
                ledger.mark_seen(category, task_id, title, status="completed")
            else:
                ledger.mark_seen(category, task_id, title, status="failed")

            if task_page != page:
                task_page.close()

        except Exception as e:
            logger.error(f"Error processing {category} task '{title}': {e}")
            ledger.mark_seen(category, task_id, title, status=f"error: {e}")

    # 2. Also check dedicated tabs/links if available (e.g. Side Menu -> Quizzes, Assignments, Practicals)
    side_menu_keywords = ["Quiz", "Assignment", "Practical"]
    for keyword in side_menu_keywords:
        try:
            menu_link = page.locator(f"a:has-text('{keyword}')").first
            if menu_link.count() > 0 and menu_link.is_visible():
                href = menu_link.get_attribute("href") or ""
                if href and not href.startswith("#") and "Dashboard" not in href:
                    logger.info(f"Navigating to dedicated section: {keyword}")
                    page.goto(urljoin(page.url, href), wait_until="networkidle", timeout=25000)
                    section_tasks = extract_tasks_from_page(page)
                    for st in section_tasks:
                        st_id = st["task_id"]
                        st_cat = st["category"]
                        st_title = st["title"]
                        if not ledger.is_seen(st_cat, st_id):
                            new_task_count += 1
                            notify_new_task(task_type=st_cat.rstrip("s"), title=st_title, task_id=st_id)
                            # Mark seen to prevent duplicate loops
                            ledger.mark_seen(st_cat, st_id, st_title, status="discovered")
        except Exception as e:
            logger.debug(f"Optional navigation to {keyword} section passed: {e}")

    return new_task_count
