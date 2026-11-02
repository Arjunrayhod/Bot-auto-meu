"""
Assignment Processor: Assist mode module.
Downloads assignment PDFs, extracts textual content via pdfplumber,
prompts LLM for comprehensive answers, and relays formatted output to Telegram.
"""

import os
import re
import logging
from typing import Optional, Tuple
from playwright.sync_api import Page, Download

from dotenv import load_dotenv
from src.solver import solve_assignment
from src.notifier import notify_assignment_solution, notify_fatal_alert

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.assignment_processor")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWNLOADS_DIR = os.path.join(BASE_DIR, "downloads")


def extract_pdf_text(filepath: str) -> str:
    """Extracts text from all pages of a PDF using pdfplumber with fallback to pypdf."""
    text_content = []
    try:
        import pdfplumber
        with pdfplumber.open(filepath) as pdf:
            for page in pdf.pages:
                t = page.extract_text()
                if t:
                    text_content.append(t)
        return "\n\n".join(text_content).strip()
    except Exception as e:
        logger.warning(f"pdfplumber extraction failed ({e}). Trying pypdf fallback...")

    try:
        from pypdf import PdfReader
        reader = PdfReader(filepath)
        for page in reader.pages:
            t = page.extract_text()
            if t:
                text_content.append(t)
        return "\n\n".join(text_content).strip()
    except Exception as e:
        logger.error(f"Failed to extract PDF text with pypdf: {e}")
        return ""


def download_assignment_pdf(page: Page, task_id: str) -> Optional[str]:
    """
    Finds and downloads attached assignment PDF files to /downloads/.
    """
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)

    # Common PDF download triggers on ASP.NET WebForms (including meu.gnums.co.in)
    pdf_selectors = [
        "a[id*='lbtnDocumentPathDownload']",
        "a[id*='lnkDownload']",
        "a[id*='btnDownload']",
        "a[href*='.pdf']",
        "input[value*='Download']",
        "a:has-text('Download')",
        "a:has-text('View Document')",
        "a:has-text('.pdf')",
    ]

    for sel in pdf_selectors:
        loc = page.locator(sel)
        if loc.count() > 0 and loc.first.is_visible():
            try:
                logger.info(f"Triggering PDF download with selector: {sel}")
                with page.expect_download(timeout=10000) as download_info:
                    loc.first.click()
                download: Download = download_info.value
                filename = download.suggested_filename or f"assignment_{task_id}.pdf"
                dest_path = os.path.join(DOWNLOADS_DIR, f"{task_id}_{filename}")
                download.save_as(dest_path)
                logger.info(f"Downloaded PDF saved to: {dest_path}")
                return dest_path
            except Exception as e:
                logger.debug(f"Direct download trigger not caught for {sel}: {e}")

    # Fallback: Check if href is a direct URL or __doPostBack
    for sel in ["a[href*='.pdf']"]:
        loc = page.locator(sel)
        if loc.count() > 0:
            href = loc.first.get_attribute("href")
            if href and href.startswith("http"):
                try:
                    import requests
                    cookies = {c["name"]: c["value"] for c in page.context.cookies()}
                    resp = requests.get(href, cookies=cookies, timeout=20)
                    if resp.ok and len(resp.content) > 100:
                        dest_path = os.path.join(DOWNLOADS_DIR, f"assignment_{task_id}.pdf")
                        with open(dest_path, "wb") as f:
                            f.write(resp.content)
                        logger.info(f"Downloaded PDF via direct GET request: {dest_path}")
                        return dest_path
                except Exception as e:
                    logger.warning(f"Error fetching direct PDF href: {e}")

    logger.info("No PDF attachment found for this assignment.")
    return None


def extract_assignment_details(page: Page) -> Tuple[str, str]:
    """Extracts title and description/question text from the assignment page."""
    # Title extraction
    title = ""
    title_loc = page.locator("span[id*='lblTitle'], span[id*='lblAssignmentName'], h3, h4, .page-header").first
    if title_loc.count() > 0:
        title = title_loc.inner_text().strip()

    # Description extraction
    desc = ""
    desc_loc = page.locator(
        "span[id*='lblDescription'], div[id*='pnlDescription'], .assignment-description, td.desc, .table-responsive"
    )
    if desc_loc.count() > 0 and desc_loc.first.is_visible():
        desc = desc_loc.first.inner_text().strip()

    if not desc:
        # Fallback to page main content text
        body_loc = page.locator("form#form1, .main-content, #content")
        if body_loc.count() > 0:
            desc = body_loc.first.inner_text().strip()[:2000]

    return title, desc


def process_assignment(page: Page, task_id: str, title: str) -> bool:
    """
    Main entry point for processing an Assignment in Assist Mode:
    1. Extracts description.
    2. Downloads and parses PDF if attached.
    3. Prompts LLM for an A+ solution.
    4. Delivers chunked solution to Telegram.
    (Leaves portal submission for student).
    """
    logger.info(f"Processing Assignment: '{title}' (ID: {task_id}) in Assist Mode.")

    p_title, description = extract_assignment_details(page)
    final_title = title or p_title or f"Assignment #{task_id}"

    # Download & Extract PDF
    pdf_path = download_assignment_pdf(page, task_id)
    pdf_text = ""
    pdf_name = None
    if pdf_path and os.path.exists(pdf_path):
        pdf_name = os.path.basename(pdf_path)
        logger.info(f"Extracting text from PDF: {pdf_name}")
        pdf_text = extract_pdf_text(pdf_path)
        logger.info(f"Extracted {len(pdf_text)} characters from {pdf_name}.")

    # Generate solution via LLM
    logger.info("Generating comprehensive assignment solution via LLM...")
    solution_text = solve_assignment(title=final_title, description=description, pdf_text=pdf_text)

    # Deliver to Telegram
    logger.info("Relaying assignment solution to Telegram...")
    notify_assignment_solution(
        title=final_title,
        task_id=task_id,
        solution_text=solution_text,
        pdf_name=pdf_name,
    )

    logger.info(f"Assignment '{final_title}' successfully processed and delivered to Telegram.")
    return True
