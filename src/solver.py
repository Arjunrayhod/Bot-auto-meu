"""
Solver Module: Hybrid cache (answer_bank.json) + LLM inference engine.
Supports Google Gemini (via native Generative Language API) and Groq (via OpenAI format).
"""

import os
import json
import re
import hashlib
import logging
import requests
from typing import Dict, Any, List, Optional
from dotenv import load_dotenv

load_dotenv(override=True)

logger = logging.getLogger("academic_bot.solver")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSWER_BANK_PATH = os.path.join(BASE_DIR, "state", "answer_bank.json")


# Track key rotation index
CURRENT_KEY_INDEX = 0


def get_gemini_keys() -> List[str]:
    """Retrieves all configured Gemini API keys from comma-separated list."""
    raw = os.getenv("GEMINI_API_KEY", "").strip()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return keys


def _call_gemini_api(prompt: str, system_instruction: str = "", json_mode: bool = False) -> str:
    """
    Invokes Google Gemini with:
    1. Multi-key pool rotation on rate limits (429/403).
    2. Model fallback chain (gemini-3.5-flash-lite -> gemini-2.5-flash -> gemini-1.5-flash).
    3. Strict maxOutputTokens to reduce token burn by ~90%.
    4. Emergency fallback to Groq if Gemini hits quota.
    """
    global CURRENT_KEY_INDEX
    keys = get_gemini_keys()
    if not keys:
        raise ValueError("GEMINI_API_KEY is not set in .env")

    configured_model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite").strip()
    model_pool = [configured_model]
    for fallback in ["gemini-2.5-flash", "gemini-1.5-flash", "gemini-2.0-flash"]:
        if fallback not in model_pool:
            model_pool.append(fallback)

    full_prompt = f"{system_instruction}\n\n{prompt}" if system_instruction else prompt
    payload: Dict[str, Any] = {
        "contents": [
            {
                "parts": [{"text": full_prompt}]
            }
        ],
        "generationConfig": {
            "temperature": 0.1 if json_mode else 0.2,
            # Limit token burn strictly: 180 tokens for JSON quizzes, 2500 for assignments
            "maxOutputTokens": 180 if json_mode else 2500,
        }
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"

    last_error = None

    # Try through all keys in the pool
    for key_attempt in range(len(keys)):
        active_key = keys[(CURRENT_KEY_INDEX + key_attempt) % len(keys)]

        for model in model_pool:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={active_key}"
            headers = {"Content-Type": "application/json"}

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=45)

                if resp.status_code == 429:
                    logger.warning(f"Key #{active_key[:8]}... rate-limited (429) on {model}. Rotating...")
                    last_error = f"Rate limit 429 on {model}"
                    continue
                elif resp.status_code in (401, 403):
                    logger.warning(f"Key #{active_key[:8]}... auth error ({resp.status_code}). Rotating to next key...")
                    last_error = f"Auth error {resp.status_code}"
                    break
                elif not resp.ok:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                    continue

                data = resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    continue

                text_parts = candidates[0].get("content", {}).get("parts", [])
                if text_parts:
                    CURRENT_KEY_INDEX = (CURRENT_KEY_INDEX + key_attempt) % len(keys)
                    return text_parts[0].get("text", "").strip()

            except Exception as e:
                last_error = str(e)
                continue

    # Automatic Emergency Failover to Groq if Gemini is exhausted
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if groq_key:
        logger.warning(f"Gemini pool exhausted ({last_error}). Triggering automatic failover to Groq!")
        return _call_groq_api(prompt, system_instruction=system_instruction, json_mode=json_mode)

    raise RuntimeError(f"All Gemini API keys and models exhausted. Last error: {last_error}")


def _call_groq_api(prompt: str, system_instruction: str = "", json_mode: bool = False) -> str:
    """Invokes Groq API using OpenAI client format."""
    from openai import OpenAI
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    model = os.getenv("GROQ_MODEL", "qwen/qwen3.8-27b").strip()
    base_url = os.getenv("GROQ_BASE_URL", "https://api.groq.com/openai/v1").strip()

    if not api_key:
        raise ValueError("GROQ_API_KEY is not set in .env")

    client = OpenAI(api_key=api_key, base_url=base_url)
    messages = []
    if json_mode and "json" not in (system_instruction or "").lower() and "json" not in prompt.lower():
        system_instruction = f"{system_instruction} Output strictly in JSON format.".strip()

    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    kwargs: Dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.2}
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}

    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content or ""


def _query_llm(prompt: str, system_instruction: str = "", json_mode: bool = False) -> str:
    """Routes query to configured provider (Gemini or Groq)."""
    provider = os.getenv("LLM_PROVIDER", "gemini").lower().strip()
    if provider == "groq":
        return _call_groq_api(prompt, system_instruction=system_instruction, json_mode=json_mode)
    else:
        return _call_gemini_api(prompt, system_instruction=system_instruction, json_mode=json_mode)


def _normalize_key(text: str) -> str:
    """Generates an MD5 key for cache lookups."""
    clean = re.sub(r"\s+", " ", text).strip().lower()
    return hashlib.md5(clean.encode("utf-8")).hexdigest()


class AnswerBank:
    """Manages read/write caching in answer_bank.json."""

    def __init__(self, filepath: str = ANSWER_BANK_PATH):
        self.filepath = filepath
        self.data: Dict[str, Any] = {"answers": {}}
        self._load()

    def _load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception as e:
                logger.error(f"Failed to load answer bank: {e}. Reinitializing.")
                self.data = {"answers": {}}
        else:
            self._save()

    def _save(self):
        os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Failed to save answer bank: {e}")

    def get(self, question_text: str) -> Optional[Dict[str, Any]]:
        key = _normalize_key(question_text)
        return self.data.get("answers", {}).get(key)

    def set(self, question_text: str, result: Dict[str, Any]):
        key = _normalize_key(question_text)
        if "answers" not in self.data:
            self.data["answers"] = {}
        self.data["answers"][key] = {
            "question": question_text,
            "result": result,
        }
        self._save()


cache = AnswerBank()


def solve_quiz_question(question_text: str, options: List[str]) -> Dict[str, Any]:
    """
    Solves a multiple-choice or short-answer quiz question.
    1. Checks answer_bank.json.
    2. If missing, requests LLM inference.
    3. Caches result and returns the best matching option.
    """
    cached = cache.get(question_text)
    if cached and "result" in cached:
        logger.info(f"Answer found in local cache for: '{question_text[:50]}...'")
        return cached["result"]

    formatted_options = "\n".join([f"{idx + 1}. {opt}" for idx, opt in enumerate(options)])
    prompt = f"""Question:
{question_text}

Options:
{formatted_options}

Instructions:
1. Select the single most accurate and correct option.
2. Return your output strictly as a JSON object with this exact format:
{{
  "selected_index": <0-based integer index of option>,
  "selected_option": "<exact text of selected option>",
  "reasoning": "<concise explanation in 1 sentence>"
}}
"""
    system_inst = "You are an expert academic exam solver. Always respond strictly in valid JSON."

    try:
        content = _query_llm(prompt, system_instruction=system_inst, json_mode=True)
        # Parse JSON
        result = json.loads(content)
        result["source"] = f"llm_{os.getenv('LLM_PROVIDER', 'gemini')}"

        idx = result.get("selected_index", 0)
        if not (0 <= idx < len(options)):
            sel_opt = result.get("selected_option", "").strip().lower()
            matched = False
            for i, opt in enumerate(options):
                if opt.strip().lower() == sel_opt or sel_opt in opt.strip().lower():
                    result["selected_index"] = i
                    matched = True
                    break
            if not matched:
                result["selected_index"] = 0

        cache.set(question_text, result)
        logger.info(f"LLM Solved: Option {result.get('selected_index')} -> {result.get('selected_option')}")
        return result

    except Exception as e:
        logger.error(f"Error during LLM quiz solution: {e}")
        return {
            "selected_index": 0,
            "selected_option": options[0] if options else "",
            "reasoning": f"Error: {e}",
            "source": "error_fallback",
        }


def solve_assignment(title: str, description: str, pdf_text: str = "") -> str:
    """
    Generates a comprehensive, high-scoring assignment answer based on prompt and optional PDF text.
    """
    content_parts = [
        f"Assignment Title: {title}",
        f"Assignment Description/Instructions:\n{description}",
    ]
    if pdf_text:
        content_parts.append(f"Extracted PDF Materials/Questions:\n{pdf_text[:12000]}")

    full_prompt = "\n\n".join(content_parts)

    system_instruction = (
        "You are a top-tier academic university scholar and tutor. "
        "Read the following assignment/PDF and generate a comprehensive answer for submission. "
        "Format it nicely with clear headings, structured points, full code/formulas where required, "
        "and thorough explanations suitable for an A+ grade."
    )

    try:
        return _query_llm(full_prompt, system_instruction=system_instruction, json_mode=False)
    except Exception as e:
        logger.error(f"Failed to generate assignment solution: {e}")
        return f"Error generating assignment solution via LLM: {e}"


def solve_practical(title: str, question: str) -> str:
    """
    Generates a step-by-step practical laboratory solution / code walkthrough.
    """
    system_instruction = (
        "You are an expert lab instructor and computer science engineer. "
        "Provide a step-by-step practical solution/code for the following problem. "
        "Use bullet points or numbered steps, include complete working code with comments, "
        "sample input/output, and a clear verification checklist."
    )
    prompt = f"Practical Title: {title}\nProblem Statement:\n{question}"

    try:
        return _query_llm(prompt, system_instruction=system_instruction, json_mode=False)
    except Exception as e:
        logger.error(f"Failed to generate practical solution: {e}")
        return f"Error generating practical solution via LLM: {e}"
