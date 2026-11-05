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
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from playwright.sync_api import Page, Locator

from dotenv import load_dotenv
from src.solver import solve_quiz_question
from src.notifier import (
    notify_quiz_started,
    notify_quiz_solutions,
    notify_quiz_completed,
    notify_fatal_alert,
)

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.quiz_processor")


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


def detect_subject_name(page: Page, title: str, hint_subject: Optional[str] = None) -> str:
    """
    Extracts subject/course name from page elements, task hints, or quiz title.
    """
    if hint_subject and len(hint_subject.strip()) > 2 and not any(w in hint_subject.lower() for w in ["quiz", "test", "exam", "assessment"]):
        return hint_subject.strip()

    subject_selectors = [
        "span[id*='lblSubject']",
        "span[id*='lblCourse']",
        "span[id*='Subject']",
        "span[id*='Course']",
        "span[id*='Paper']",
        "td:has-text('Subject') + td",
        "td:has-text('Course') + td",
        "div:has-text('Subject:')",
    ]
    for sel in subject_selectors:
        try:
            loc = page.locator(sel)
            if loc.count() > 0 and loc.first.is_visible():
                txt = loc.first.inner_text().strip()
                if "Subject:" in txt:
                    txt = txt.split("Subject:")[-1].strip()
                elif "Course:" in txt:
                    txt = txt.split("Course:")[-1].strip()
                if txt and len(txt) > 2 and not any(w in txt.lower() for w in ["subject", "course", "quiz", "test"]):
                    return txt
        except Exception:
            pass

    # Clean title prefixes/suffixes like 'Quiz 4 - ...' or '... - Quiz 1'
    cleaned = re.sub(r"^(?:quiz|test|assessment|mst|cia)\s*\d*\s*[-:]\s*", "", title, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s*[-:]\s*(?:quiz|test|assessment|mst|cia)\s*\d*$", "", cleaned, flags=re.IGNORECASE).strip()
    if cleaned and not any(w == cleaned.lower() for w in ["quiz", "test", "exam", "assessment"]):
        return cleaned

    return title


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


def process_quiz(page: Page, task_id: str, title: str, subject: Optional[str] = None) -> bool:
    """
    Main entry point to execute an online quiz:
    1. Detects subject name and allowed time limit.
    2. Sends immediate 'Quiz Started' alert to Telegram with subject name.
    3. Answers questions via Gemini AI with humanized delays.
    4. Delivers complete Questions & Answers to Telegram.
    5. Waits for safety timer boundary (e.g. 50%).
    6. Submits and records results.
    """
    logger.info(f"Starting Quiz Processor for: '{title}' (ID: {task_id})")
    start_time = time.time()

    # Automatically accept JavaScript alerts / confirm dialogs on submit
    page.on("dialog", lambda dialog: dialog.accept())

    # Detect subject name
    subject_name = detect_subject_name(page, title, hint_subject=subject)
    logger.info(f"Detected Subject: '{subject_name}'")

    # Detect time limit and compute safety duration
    safety_ratio = float(os.getenv("QUIZ_SAFETY_TIMER_RATIO", "0.50"))
    time_limit_sec = parse_time_limit_seconds(page)
    if time_limit_sec:
        target_duration = int(time_limit_sec * safety_ratio)
        logger.info(f"Quiz time limit detected: {time_limit_sec}s. Target submission time: {target_duration}s ({int(safety_ratio * 100)}%).")
    else:
        # Default safety duration if no explicit countdown is found
        target_duration = random.randint(120, 240)
        logger.info(f"No explicit countdown found. Defaulting safety pacing to {target_duration}s.")

    # Calculate exact clock times for 50% submission and safe user login
    now_dt = datetime.now()
    submit_dt = now_dt + timedelta(seconds=target_duration)
    safe_login_dt = submit_dt + timedelta(minutes=1)
    submit_time_str = submit_dt.strftime("%I:%M %p")
    safe_login_time_str = safe_login_dt.strftime("%I:%M %p")

    # 1. Immediate Telegram Alert: Quiz Started
    try:
        notify_quiz_started(
            subject=subject_name,
            title=title,
            task_id=task_id,
            time_limit_sec=time_limit_sec,
            target_duration_sec=target_duration,
            submit_time_str=submit_time_str,
            safe_login_time_str=safe_login_time_str,
        )
    except Exception as e:
        logger.warning(f"Failed to send quiz started alert: {e}")

    # Check if quiz opened in another tab/popup
    try:
        for p in page.context.pages:
            if p != page and p.url and "about:blank" not in p.url and "Dashboard" not in p.url:
                logger.info(f"Switching to active quiz popup/tab: {p.url}")
                page = p
                page.on("dialog", lambda dialog: dialog.accept())
                break
    except Exception as e:
        logger.debug(f"Error checking pages: {e}")

    logger.info(f"Active quiz page URL: {page.url} | Title: {page.title()}")

    # 2. Extract questions (with instruction screen and frame fallback)
    questions = extract_questions_from_page(page)
    if not questions:
        logger.info("Questions not found on initial view. Checking for instruction agreement or start buttons...")
        # Check agreement checkbox
        try:
            checkboxes = page.locator("input[type='checkbox']")
            for cb_i in range(checkboxes.count()):
                cb = checkboxes.nth(cb_i)
                if not cb.is_checked():
                    cb.check(force=True)
                    logger.info("Checked exam instruction checkbox.")
        except Exception as e:
            logger.debug(f"Checkbox check error: {e}")

        # Click Start / Proceed / Agree / Confirm / Take Test button
        start_btns = page.locator(
            "input[id*='btnAgree'], button:has-text('Agree'), a:has-text('Agree'), "
            "input[id*='btnConfirm'], button:has-text('Confirm'), a:has-text('Confirm'), input[value*='Confirm'], "
            "input[type='submit'][value*='Start'], button:has-text('Start'), a:has-text('Start'), "
            "input[value*='Proceed'], button:has-text('Proceed'), a:has-text('Proceed'), "
            "input[value*='Continue'], button:has-text('Continue'), "
            "input[id*='btnStart'], input[id*='btnTakeTest'], a[id*='btnStart']"
        )
        if start_btns.count() > 0:
            for b_i in range(start_btns.count()):
                b = start_btns.nth(b_i)
                try:
                    if b.is_visible():
                        b_txt = b.inner_text().strip() or b.get_attribute("value") or "Start"
                        logger.info(f"Clicking exam start button: '{b_txt}'")
                        b.click()
                        page.wait_for_timeout(5000)
                        # Check if a new tab/popup opened after clicking start/agree
                        try:
                            for p in page.context.pages:
                                if p != page and p.url and "about:blank" not in p.url and "Dashboard" not in p.url:
                                    logger.info(f"Switched to active quiz popup/tab after start: {p.url}")
                                    page = p
                                    page.on("dialog", lambda dialog: dialog.accept())
                                    break
                        except Exception as e:
                            logger.debug(f"Error switching page after start: {e}")
                        break
                except Exception as e:
                    logger.debug(f"Start button click: {e}")

        try:
            page.wait_for_selector("input[type='radio'], div.card, tr:has(input[type='radio'])", timeout=10000)
        except Exception:
            pass

        questions = extract_questions_from_page(page)

    # Frame fallback
    if not questions and hasattr(page, "frames"):
        for fr in page.frames:
            if fr != page.main_frame:
                try:
                    f_qs = extract_questions_from_page(fr)
                    if f_qs:
                        logger.info(f"Extracted {len(f_qs)} questions from iframe: {fr.url}")
                        questions = f_qs
                        break
                except Exception:
                    pass

    if not questions:
        logger.error(f"Could not parse any questions on page for quiz '{title}'. URL: {page.url}")
        return False

    logger.info(f"Successfully extracted {len(questions)} questions. Beginning answering loop...")

    solved_qa = []
    # 3. Answering loop with human delays
    for i, q in enumerate(questions, 1):
        q_text = q["text"]
        opts_text = [o["text"] for o in q["options"]]

        logger.info(f"Solving Q{i}/{len(questions)}: '{q_text[:60]}...'")
        sol = solve_quiz_question(q_text, opts_text)
        selected_index = sol.get("selected_index", 0)
        reasoning = sol.get("reasoning", "")

        selected_opt_text = "N/A"
        if 0 <= selected_index < len(q["options"]):
            selected_opt_text = q["options"][selected_index]["text"]
            target_radio: Locator = q["options"][selected_index]["locator"]
            # Human-like delay before click
            delay = random.uniform(2.0, 4.5)
            logger.debug(f"Pacing delay {delay:.2f}s before answering Q{i}...")
            time.sleep(delay)

            try:
                target_radio.scroll_into_view_if_needed()
                target_radio.click(force=True)
                logger.info(f"Selected option {selected_index + 1}: {selected_opt_text}")
            except Exception as e:
                logger.warning(f"Error clicking option {selected_index} for Q{i}: {e}")
        elif opts_text:
            selected_opt_text = opts_text[0]

        solved_qa.append({
            "num": i,
            "question": q_text,
            "selected_opt": selected_opt_text,
            "reason": reasoning,
        })

    # 4. Deliver complete Questions & Answers to Telegram with exact login timer
    try:
        notify_quiz_solutions(
            subject=subject_name,
            title=title,
            task_id=task_id,
            qa_list=solved_qa,
            submit_time_str=submit_time_str,
            safe_login_time_str=safe_login_time_str,
        )
    except Exception as e:
        logger.error(f"Failed to send quiz solutions to Telegram: {e}")

    # 5. Safety Timer Wait: Wait until safety ratio (50%) of allowed time has elapsed
    elapsed = time.time() - start_time
    remaining_wait = target_duration - elapsed
    if remaining_wait > 0:
        logger.info(f"Holding for safety timer: Waiting {remaining_wait:.1f}s to reach {int(safety_ratio * 100)}% threshold...")
        time.sleep(remaining_wait)

    total_elapsed = int(time.time() - start_time)
    elapsed_str = f"{total_elapsed // 60}m {total_elapsed % 60}s"

    # 6. Submit Quiz
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

    # Handle post-submit confirmation popup / modal ("OK" / "Yes" button)
    page.wait_for_timeout(1500)
    ok_selectors = [
        "button.confirm",
        ".swal-button--confirm",
        "button:has-text('OK')",
        "button:has-text('Ok')",
        "button:has-text('Yes')",
        "a:has-text('OK')",
        "a:has-text('Ok')",
        "input[value='OK']",
        "input[value='Ok']",
        "input[value='Yes']",
    ]
    for ok_sel in ok_selectors:
        ok_btn = page.locator(ok_sel)
        if ok_btn.count() > 0 and ok_btn.first.is_visible():
            try:
                logger.info(f"Clicking final confirmation button: '{ok_sel}'")
                ok_btn.first.click()
                page.wait_for_timeout(3000)
                break
            except Exception as e:
                logger.debug(f"OK button click note: {e}")

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
    notify_quiz_completed(title=title, task_id=task_id, score=score, elapsed=elapsed_str, subject=subject_name)
    return True
