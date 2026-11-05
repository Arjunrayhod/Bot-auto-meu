"""
24/7 Supervised Watchdog & Auto-Healing Process Manager:
Ensures the Academic & Quiz Bot daemon NEVER stops running.
- Prevents Windows system standby/sleep using SetThreadExecutionState.
- Detects crashes or unexpected termination of the bot process.
- Instantly auto-respawns the bot within 5 seconds.
- Alerts Telegram whenever an auto-recovery event occurs.
"""

import os
import sys
import time
import ctypes
import signal
import logging
import subprocess
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

load_dotenv(os.path.join(BASE_DIR, ".env"), override=True)

# Prevent Windows Sleep: ES_CONTINUOUS (0x80000000) | ES_SYSTEM_REQUIRED (0x00000001)
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_AWAYMODE_REQUIRED = 0x00000040

def prevent_windows_sleep():
    """Instructs Windows power management to keep CPU running."""
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        )
        logger.info("Windows system sleep prevention active (ES_CONTINUOUS | ES_SYSTEM_REQUIRED).")
    except Exception as e:
        logger.warning(f"Could not set thread execution state: {e}")

# Configure Watchdog Logging
_handlers = [logging.FileHandler(os.path.join(BASE_DIR, "watchdog.log"), encoding="utf-8")]
if sys.stdout is not None:
    _handlers.append(logging.StreamHandler(sys.stdout))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] [%(levelname)s]: %(message)s",
    handlers=_handlers,
)
logger = logging.getLogger("academic_bot.watchdog")

from src.notifier import send_telegram_message

RUNNING = True
current_process = None

def handle_shutdown(signum, frame):
    global RUNNING, current_process
    logger.info("Watchdog shutdown requested. Terminating child bot process...")
    RUNNING = False
    if current_process and current_process.poll() is None:
        try:
            current_process.terminate()
            current_process.wait(timeout=5)
        except Exception:
            current_process.kill()
    sys.exit(0)

try:
    signal.signal(signal.SIGINT, handle_shutdown)
    signal.signal(signal.SIGTERM, handle_shutdown)
except Exception:
    pass

def ensure_single_watchdog():
    """Kills any older running watchdog instance to avoid duplicate listeners."""
    import psutil
    me = os.getpid()
    for p in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            if p.info['pid'] != me:
                cmdline = p.info.get('cmdline') or []
                if any(arg.endswith('watchdog.py') for arg in cmdline) and not any(arg == '-c' for arg in cmdline):
                    logger.warning(f"Terminating older duplicate watchdog instance (PID {p.info['pid']})...")
                    p.kill()
        except Exception:
            pass

def run_watchdog():
    global current_process, RUNNING
    ensure_single_watchdog()
    prevent_windows_sleep()
    logger.info("==================================================")
    logger.info("   24/7 SELF-HEALING BOT WATCHDOG ACTIVATED")
    logger.info("==================================================")

    # Single-instance check via simple lockfile
    lock_file = os.path.join(BASE_DIR, "state", "watchdog.pid")
    os.makedirs(os.path.dirname(lock_file), exist_ok=True)
    with open(lock_file, "w") as f:
        f.write(str(os.getpid()))

    crash_count = 0
    poll_interval = int(os.getenv("CHECK_INTERVAL_SECONDS", "300"))

    while RUNNING:
        cmd = [sys.executable, "-m", "src.main", "--daemon", "--interval", str(poll_interval)]
        logger.info(f"Spawning child bot daemon: {' '.join(cmd)}")
        start_time = time.time()

        try:
            creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            current_process = subprocess.Popen(
                cmd,
                cwd=BASE_DIR,
                stdout=None,
                stderr=None,
                creationflags=creationflags,
            )
            # Wait for child process to terminate
            ret_code = current_process.wait()
            elapsed = time.time() - start_time
            logger.warning(f"Bot daemon process exited with return code {ret_code} after {elapsed:.1f}s.")

            if not RUNNING:
                break

            crash_count += 1
            # Send recovery alert to Telegram
            recovery_alert = (
                f"🛡️ <b>WATCHDOG: BOT AUTO-RECOVERY TRIGGERED!</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <b>Event:</b> Bot process exited (Exit Code: {ret_code})\n"
                f"⏱️ <b>Uptime before exit:</b> {int(elapsed // 60)}m {int(elapsed % 60)}s\n"
                f"🔄 <b>Action:</b> Watchdog is automatically restarting the bot in 5 seconds.\n"
                f"🟢 <b>Status:</b> 24/7 Continuous Monitoring stays ACTIVE!"
            )
            try:
                send_telegram_message(recovery_alert, parse_mode="HTML")
            except Exception as e:
                logger.error(f"Failed to send watchdog Telegram alert: {e}")

            time.sleep(5)
            prevent_windows_sleep()

        except Exception as e:
            logger.error(f"Exception managing child process: {e}", exc_info=True)
            time.sleep(5)

if __name__ == "__main__":
    run_watchdog()
