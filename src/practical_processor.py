"""
Practical Processor: Assist mode module for laboratory & practical sessions.
Extracts practical problem statements, requests step-by-step code and execution steps
from the LLM, and sends the formatted output to Telegram.
"""

import logging
from typing import Tuple
from playwright.sync_api import Page

from src.solver import solve_practical
from src.notifier import notify_practical_solution

logger = logging.getLogger("academic_bot.practical_processor")


def extract_practical_details(page: Page) -> Tuple[str, str]:
    """Extracts title and problem statement/requirements for the practical."""
    # Title
    title = ""
    title_loc = page.locator("span[id*='lblTitle'], span[id*='lblPracticalName'], h3, h4, .page-header").first
    if title_loc.count() > 0:
        title = title_loc.inner_text().strip()

    # Problem statement / question
    question = ""
    q_loc = page.locator(
        "span[id*='lblAim'], span[id*='lblProblem'], span[id*='lblQuestion'], div.practical-desc, .table-responsive"
    )
    if q_loc.count() > 0 and q_loc.first.is_visible():
        question = q_loc.first.inner_text().strip()

    if not question:
        # Fallback to main content container
        content_loc = page.locator("form#form1, .main-content, #content")
        if content_loc.count() > 0:
            question = content_loc.first.inner_text().strip()[:2000]

    return title, question


def process_practical(page: Page, task_id: str, title: str) -> bool:
    """
    Main entry point for processing a Practical in Assist Mode:
    1. Extracts practical objective / code prompt.
    2. Prompts LLM for step-by-step solution, working code, and sample outputs.
    3. Sends formatted solution to Telegram.
    """
    logger.info(f"Processing Practical: '{title}' (ID: {task_id}) in Assist Mode.")

    p_title, question_text = extract_practical_details(page)
    final_title = title or p_title or f"Practical #{task_id}"

    if not question_text:
        question_text = f"Practical experiment: {final_title}"

    # LLM Inference
    logger.info("Generating step-by-step practical solution via LLM...")
    solution_text = solve_practical(title=final_title, question=question_text)

    # Deliver to Telegram
    logger.info("Relaying practical solution to Telegram...")
    notify_practical_solution(
        title=final_title,
        task_id=task_id,
        solution_text=solution_text,
    )

    logger.info(f"Practical '{final_title}' successfully processed and delivered to Telegram.")
    return True
