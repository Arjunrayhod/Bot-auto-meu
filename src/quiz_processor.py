"""
Quiz Processor: Autonomous quiz solving and submission module.
Extracts questions and options, retrieves solutions via solver.py, applies human delays,
and respects the 80% safety timer limit before submitting.
"""

import os
import re
import time
import random
import logging
from typing import Dict, Any, List, Optional
from playwright.sync_api import Page, Locator

from dotenv import load_dotenv
from src.solver import solve_quiz_question
from src.notifier import notify_quiz_completed, notify_fatal_alert

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.quiz_processor")

SAFETY_RATIO = float(os.getenv("QUIZ_SAFETY_TIMER_RATIO", "0.80"))


def parse_time_limit_seconds(page: Page) -> Optional[int]:
    """
    Attempts to detect the quiz duration or remaining countdown timer from the page.
    Looks for labels such as 'Time Remaining', 'Duration: 30 Mins', etc.
    """
    timer_selectors = [
        "span[id*='Timer']",
        "span[id*='TimeRemaining']",
        "div[id*='timer']",
        ".timer",
        ".countdown",
        "span:has-text('Time Left')",
        "span:has-text('Duration')",
    ]
    for sel in timer_selectors:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            txt = loc.first.inner_text().strip()
            # Match HH:MM:SS or MM:SS
            match_clock = re.search(r"(\d{1,2}):(\d{2}):(\d{2})", txt)
            if match_clock:
                h, m, s = map(int, match_clock.groups())
                return h * 3600 + m * 60 + s
            match_ms = re.search(r"(\d{1,2}):(\d{2})", txt)
            if match_ms:
                m, s = map(int, match_ms.groups())
                return m * 60 + s
            # Match "X Minutes"
            match_mins = re.search(r"(\d+)\s*(?:min|minute)", txt, re.IGNORECASE)
            if match_mins:
                return int(match_mins.group(1)) * 60

    return None


def extract_questions_from_page(page: Page) -> List[Dict[str, Any]]:
    """
    Parses questions and their respective option radio buttons / checkboxes / inputs.
    Tolerates various WebForms layout templates (tables, div cards, gridviews).
    """
    questions = []

    # Strategy 1: Container cards or question panels
    container_selectors = [
        "div.question-block",
        "div.question-card",
        "div[id*='pnlQuestion']",
        "table[id*='Question']",
        "div.card:has(input[type='radio'])",
        "div.panel:has(input[type='radio'])",
        "tr:has(input[type='radio'])",
    ]

    containers = None
    for sel in container_selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            containers = loc
            break

    if containers and containers.count() > 0:
        total = containers.count()
        logger.info(f"Detected {total} question containers.")
        for idx in range(total):
            cont = containers.nth(idx)
            # Question text
            q_text_loc = cont.locator("span[id*='lblQuestion'], .question-text, h4, h5, strong, p").first
            q_text = q_text_loc.inner_text().strip() if q_text_loc.count() > 0 else cont.inner_text().split("\n")[0]

            # Options
            options = []
            radio_locs = cont.locator("input[type='radio']")
            radio_count = radio_locs.count()

            for r_idx in range(radio_count):
                radio = radio_locs.nth(r_idx)
                # Attempt to find adjacent label text
                radio_id = radio.get_attribute("id") or ""
                opt_text = ""
                if radio_id:
                    lbl = cont.locator(f"label[for='{radio_id}']")
                    if lbl.count() > 0:
                        opt_text = lbl.first.inner_text().strip()

                if not opt_text:
                    # Look for parent label or nearby sibling text
                    parent = radio.locator("..")
                    opt_text = parent.inner_text().strip()

                options.append({
                    "index": r_idx,
                    "text": opt_text or f"Option {r_idx + 1}",
                    "locator": radio,
                })

            if q_text and options:
                questions.append({
                    "id": f"q_{idx + 1}",
                    "text": q_text,
                    "options": options,
                    "container": cont,
                })

    # Strategy 2: Fallback to all radio button groups on page
    if not questions:
        logger.info("Using fallback radio-group grouping...")
        radios = page.locator("input[type='radio']")
        groups: Dict[str, List[Locator]] = {}
        for i in range(radios.count()):
            r = radios.nth(i)
            name = r.get_attribute("name") or "default_group"
            groups.setdefault(name, []).append(r)

        for g_idx, (name, r_list) in enumerate(groups.items()):
            first_r = r_list[0]
            # Try to grab preceding text or header
            q_text = f"Question {g_idx + 1}"
            parent_table = first_r.locator("xpath=./ancestor::table[1]")
            if parent_table.count() > 0:
                header = parent_table.locator("th, td.header, caption").first
                if header.count() > 0:
                    q_text = header.inner_text().strip()

            options = []
            for o_idx, r in enumerate(r_list):
                r_id = r.get_attribute("id") or ""
                lbl_text = ""
                if r_id:
                    lbl = page.locator(f"label[for='{r_id}']")
                    if lbl.count() > 0:
                        lbl_text = lbl.first.inner_text().strip()
                if not lbl_text:
                    lbl_text = r.locator("..").inner_text().strip()

                options.append({
                    "index": o_idx,
                    "text": lbl_text or f"Option {o_idx + 1}",
                    "locator": r,
                })

            questions.append({
                "id": name,
                "text": q_text,
                "options": options,
            })

    return questions


def process_quiz(page: Page, task_id: str, title: str) -> bool:
    """
    Main entry point to execute an online quiz:
    1. Parses total questions & allowed time limit.
    2. Answers questions with humanized delays.
    3. Waits for 80% safety timer boundary.
    4. Submits and records results.
    """
    logger.info(f"Starting Quiz Processor for: '{title}' (ID: {task_id})")
    start_time = time.time()

    # Automatically accept JavaScript alerts / confirm dialogs on submit
    page.on("dialog", lambda dialog: dialog.accept())

    # Detect time limit
    time_limit_sec = parse_time_limit_seconds(page)
    if time_limit_sec:
        target_duration = int(time_limit_sec * SAFETY_RATIO)
        logger.info(f"Quiz time limit detected: {time_limit_sec}s. Target submission time: {target_duration}s (80%).")
    else:
        # Default safety duration if no explicit countdown is found
        target_duration = random.randint(120, 240)
        logger.info(f"No explicit countdown found. Defaulting safety pacing to {target_duration}s.")

    # Extract questions
    questions = extract_questions_from_page(page)
    if not questions:
        logger.error(f"Could not parse any questions on page for quiz '{title}'.")
        return False

    logger.info(f"Successfully extracted {len(questions)} questions. Beginning answering loop...")

    # Answer loop with human delays
    for i, q in enumerate(questions, 1):
        q_text = q["text"]
        opts_text = [o["text"] for o in q["options"]]

        logger.info(f"Solving Q{i}/{len(questions)}: '{q_text[:60]}...'")
        sol = solve_quiz_question(q_text, opts_text)
        selected_index = sol.get("selected_index", 0)

        if 0 <= selected_index < len(q["options"]):
            target_radio: Locator = q["options"][selected_index]["locator"]
            # Human-like delay before click
            delay = random.uniform(2.0, 4.5)
            logger.debug(f"Pacing delay {delay:.2f}s before answering Q{i}...")
            time.sleep(delay)

            try:
                target_radio.scroll_into_view_if_needed()
                target_radio.click(force=True)
                logger.info(f"Selected option {selected_index + 1}: {q['options'][selected_index]['text']}")
            except Exception as e:
                logger.warning(f"Error clicking option {selected_index} for Q{i}: {e}")

    # Safety Timer Wait: Wait until 80% of allowed time has elapsed
    elapsed = time.time() - start_time
    remaining_wait = target_duration - elapsed
    if remaining_wait > 0:
        logger.info(f"Holding for safety timer: Waiting {remaining_wait:.1f}s to reach {SAFETY_RATIO * 100}% threshold...")
        time.sleep(remaining_wait)

    total_elapsed = int(time.time() - start_time)
    elapsed_str = f"{total_elapsed // 60}m {total_elapsed % 60}s"

    # Submit Quiz
    logger.info("Submitting quiz...")
    submit_selectors = [
        "input[type='submit'][id*='btnSubmit']",
        "button[id*='btnSubmit']",
        "input[value*='Submit']",
        "button:has-text('Submit')",
        "input[type='submit']",
    ]
    submitted = False
    for sel in submit_selectors:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            try:
                with page.expect_navigation(timeout=30000):
                    loc.first.click()
                submitted = True
                break
            except Exception:
                loc.first.click()
                page.wait_for_timeout(3000)
                submitted = True
                break

    if not submitted:
        logger.error("Could not find or click Submit button for quiz.")
        notify_fatal_alert(f"Failed to submit quiz '{title}'. Check portal manually.")
        return False

    # Check for score or confirmation
    score = None
    score_loc = page.locator("span[id*='lblScore'], span[id*='lblMarks'], .score, .result")
    if score_loc.count() > 0 and score_loc.first.is_visible():
        score = score_loc.first.inner_text().strip()

    logger.info(f"Quiz '{title}' submitted successfully! Elapsed: {elapsed_str}, Score: {score or 'Recorded'}")
    notify_quiz_completed(title=title, task_id=task_id, score=score, elapsed=elapsed_str)
    return True
