"""
BookMyShow IMAX Odyssey Ticket Watcher
=======================================
Watches PVR Palladium Mall, Ahmedabad for The Odyssey (IMAX) tickets on Aug 1, 2026.
Sends an email alert to all configured recipients the moment tickets go live.

Usage:
  python watcher.py                # Loop mode: checks every CHECK_INTERVAL_MINUTES
  python watcher.py --single-check # Run once (used by GitHub Actions)
  python watcher.py --test         # Send a test email immediately
"""

import os
import re
import sys
import time
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

# Load .env file if it exists (for local runs)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # On GitHub Actions, env vars are injected directly

# ─── Configuration ────────────────────────────────────────────────────────────
SENDER_EMAIL          = os.getenv("SENDER_EMAIL", "")
SENDER_APP_PASSWORD   = os.getenv("SENDER_APP_PASSWORD", "")
RECIPIENT_EMAILS      = [e.strip() for e in os.getenv("RECIPIENT_EMAILS", "").split(",") if e.strip()]
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

BMS_URL        = "https://in.bookmyshow.com/cinemas/ahmedabad/pvr-palladium-mall-ahmedabad/buytickets/PPAM/20260729"
TARGET_MOVIE   = "odyssey"   # case-insensitive substring match
TARGET_FORMAT  = "imax"      # must also appear near the movie name

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler("watcher.log", encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ─── Scraping ─────────────────────────────────────────────────────────────────

def _parse_shows(html: str) -> list[str]:
    """
    Parse the rendered HTML and return a list of show-time strings for
    any 'Odyssey' + 'IMAX' combination.  Returns an empty list if not found.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    # Quick gate: both keywords must appear somewhere on the page
    if TARGET_MOVIE not in page_text.lower() or TARGET_FORMAT not in page_text.lower():
        return []

    show_times: list[str] = []

    # BMS renders each movie as a row / card; walk up from every element that
    # contains "odyssey" and look for an IMAX sibling/ancestor section.
    odyssey_nodes = soup.find_all(
        string=lambda t: t and TARGET_MOVIE in t.lower()
    )

    for node in odyssey_nodes:
        # Walk up the DOM tree to find a block that also contains "IMAX"
        ancestor = node.find_parent()
        for _ in range(12):
            if ancestor is None:
                break
            ancestor_text = ancestor.get_text(" ", strip=True)
            if TARGET_FORMAT in ancestor_text.lower():
                # Extract all HH:MM AM/PM tokens from this block
                times = re.findall(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", ancestor_text, re.IGNORECASE)
                if times:
                    show_times.extend(t.upper() for t in times)
                break
            ancestor = ancestor.find_parent()

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_times: list[str] = []
    for t in show_times:
        if t not in seen:
            seen.add(t)
            unique_times.append(t)

    return unique_times


def check_with_requests() -> list[str] | None:
    """
    Fast path: plain HTTP request + BeautifulSoup.
    Returns None if the page isn't JS-rendered (fallback to Playwright needed).
    Returns [] if rendered but Odyssey IMAX not found.
    Returns [times] if found.
    """
    import requests
    from bs4 import BeautifulSoup

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/138.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://in.bookmyshow.com/",
        "Cache-Control": "no-cache",
    }

    resp = requests.get(BMS_URL, headers=headers, timeout=30)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    body_text = soup.get_text(" ", strip=True).lower()

    # Heuristic: if BMS served a real rendered page it will contain "showtime" or show-time buttons
    page_seems_rendered = (
        "showtime" in body_text
        or "__movie-name" in resp.text
        or "show-details" in resp.text
    )
    if not page_seems_rendered:
        log.info("requests: page doesn't appear fully rendered — will try Playwright.")
        return None

    return _parse_shows(resp.text)


def check_with_playwright() -> list[str]:
    """
    Slow path: headless Chromium via Playwright.
    Always returns a list (empty = not found).
    """
    from playwright.sync_api import sync_playwright

    log.info("Launching headless Chromium via Playwright…")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
        )
        page = ctx.new_page()

        log.info(f"Navigating to: {BMS_URL}")
        page.goto(BMS_URL, wait_until="networkidle", timeout=90_000)

        # Try to wait for movie listing elements
        selectors = [
            "[class*='show-details']",
            "[class*='__movie-name']",
            "[class*='movie-name']",
            "[class*='showtime']",
        ]
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=8_000)
                log.info(f"Playwright: found selector '{sel}'")
                break
            except Exception:
                pass

        page.wait_for_timeout(3_000)  # Extra settle time for lazy-loaded content
        html = page.content()
        browser.close()

    return _parse_shows(html)


def check_tickets() -> tuple[bool, list[str]]:
    """
    Try the fast path first, fall back to Playwright.
    Returns (found: bool, show_times: list[str]).
    """
    log.info("🔍 Checking BMS for The Odyssey (IMAX) on Aug 1…")

    show_times: list[str] = []

    # 1. Fast path
    try:
        result = check_with_requests()
        if result is not None:
            show_times = result
        else:
            raise RuntimeError("Page not rendered — need Playwright")
    except Exception as e:
        log.info(f"requests fallback: {e}")
        # 2. Playwright path
        try:
            show_times = check_with_playwright()
        except Exception as pw_err:
            log.error(f"Playwright failed: {pw_err}")
            return False, []

    if show_times:
        log.info(f"✅ ODYSSEY IMAX FOUND! Show times: {show_times}")
        return True, show_times
    else:
        log.info("❌ Odyssey IMAX not yet listed.")
        return False, []


# ─── Email ────────────────────────────────────────────────────────────────────

def send_email(show_times: list[str], is_test: bool = False) -> bool:
    """Send an HTML alert email to all configured recipients."""
    if not SENDER_EMAIL or not SENDER_APP_PASSWORD:
        log.error("❌ Email credentials not set — check SENDER_EMAIL / SENDER_APP_PASSWORD.")
        return False
    if not RECIPIENT_EMAILS:
        log.error("❌ No RECIPIENT_EMAILS configured.")
        return False

    prefix = "[TEST] " if is_test else ""
    subject = f"{prefix}🎬 IMAX Odyssey Tickets LIVE! Book Now — PVR Palladium Ahmedabad, Aug 1"

    if show_times:
        times_html = (
            "<ul style='font-size:18px;color:#4ade80;line-height:1.8'>"
            + "".join(f"<li><strong>{t}</strong></li>" for t in show_times)
            + "</ul>"
        )
    else:
        times_html = "<p style='color:#ccc'>Open BookMyShow to see all available slots.</p>"

    detected_at = datetime.now().strftime("%d %b %Y, %I:%M %p IST")

    html_body = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0d0d0d;font-family:'Segoe UI',Arial,sans-serif">
  <div style="max-width:620px;margin:30px auto;background:#111827;border-radius:20px;overflow:hidden;box-shadow:0 20px 60px rgba(0,0,0,.6)">

    <!-- Header -->
    <div style="background:linear-gradient(135deg,#c0392b 0%,#8e0000 100%);padding:40px 30px;text-align:center">
      <div style="font-size:48px;margin-bottom:8px">🎬</div>
      <h1 style="margin:0;color:#fff;font-size:30px;letter-spacing:1px">THE ODYSSEY — IMAX</h1>
      <p style="margin:10px 0 0;color:#ffcdd2;font-size:15px">Tickets are LIVE! Don't miss out!</p>
    </div>

    <!-- Body -->
    <div style="padding:32px 30px">

      <table style="width:100%;border-collapse:collapse;margin-bottom:24px">
        <tr>
          <td style="padding:8px 0;color:#9ca3af;font-size:14px">📍 Venue</td>
          <td style="padding:8px 0;color:#f3f4f6;font-size:14px;font-weight:600">PVR Palladium Mall, Ahmedabad</td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#9ca3af;font-size:14px">📅 Date</td>
          <td style="padding:8px 0;color:#f3f4f6;font-size:14px;font-weight:600">Saturday, 1 August 2026</td>
        </tr>
        <tr>
          <td style="padding:8px 0;color:#9ca3af;font-size:14px">🎭 Format</td>
          <td style="padding:8px 0;color:#f3f4f6;font-size:14px;font-weight:600">IMAX 2D · English</td>
        </tr>
      </table>

      <h3 style="color:#fff;margin-bottom:12px">🕐 Available Show Times:</h3>
      {times_html}

      <!-- CTA Button -->
      <div style="text-align:center;margin:36px 0 20px">
        <a href="{BMS_URL}"
           style="display:inline-block;background:linear-gradient(135deg,#e53935,#b71c1c);
                  color:#fff;padding:18px 48px;border-radius:50px;text-decoration:none;
                  font-size:18px;font-weight:700;letter-spacing:.5px;
                  box-shadow:0 8px 24px rgba(229,57,53,.4)">
          🎟️ Book Now on BookMyShow
        </a>
      </div>

      <p style="color:#4b5563;font-size:12px;text-align:center;margin-top:24px;border-top:1px solid #1f2937;padding-top:16px">
        Detected at {detected_at} · Sent by BMS Watcher
        {' · <strong style="color:#f59e0b">TEST EMAIL</strong>' if is_test else ''}
      </p>
    </div>
  </div>
</body>
</html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"BMS Watcher 🎬 <{SENDER_EMAIL}>"
    msg["To"]      = ", ".join(RECIPIENT_EMAILS)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.login(SENDER_EMAIL, SENDER_APP_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, msg.as_string())
        log.info(f"📧 Email sent to: {', '.join(RECIPIENT_EMAILS)}")
        return True
    except smtplib.SMTPAuthenticationError:
        log.error("❌ Gmail auth failed — check SENDER_APP_PASSWORD (must be App Password, not account password).")
        return False
    except Exception as exc:
        log.error(f"❌ Email send error: {exc}")
        return False


# ─── Entry Points ─────────────────────────────────────────────────────────────

def mode_test():
    """Send a test email with dummy show times."""
    log.info("🧪 TEST MODE — sending test email…")
    dummy_times = ["09:00 AM", "03:45 PM", "07:15 PM", "10:45 PM"]
    ok = send_email(dummy_times, is_test=True)
    if ok:
        log.info("✅ Test email sent! Check your inbox (and spam folder).")
    else:
        log.error("❌ Test email failed. Fix credentials in .env and retry.")
    sys.exit(0 if ok else 1)


def mode_single_check():
    """
    Run exactly one check (used by GitHub Actions).
    Exits 0 if tickets found + email sent, exits 1 otherwise.
    """
    found, show_times = check_tickets()
    if found:
        ok = send_email(show_times)
        sys.exit(0 if ok else 2)
    else:
        sys.exit(1)  # Not found — GitHub Actions interprets this as "not done yet" (we suppress failure)


def mode_loop():
    """Continuous loop for local use."""
    log.info(f"🚀 BMS Watcher started — checking every {CHECK_INTERVAL_MINUTES} min.")
    log.info(f"   Watching : {BMS_URL}")
    log.info(f"   Notifying: {', '.join(RECIPIENT_EMAILS) or '(no recipients set!)'}")
    log.info("   Press Ctrl+C to stop.\n")

    attempt = 0
    while True:
        attempt += 1
        log.info(f"── Check #{attempt} ──")
        found, show_times = check_tickets()

        if found:
            ok = send_email(show_times)
            if ok:
                log.info("🎉 Notification sent! Watcher will keep running in case more slots open.")
                # Keep running so friends who act slowly still get reminders — optional: break here
            else:
                log.warning("Email failed — will retry on next check.")

        log.info(f"⏰ Sleeping {CHECK_INTERVAL_MINUTES} min until next check…\n")
        try:
            time.sleep(CHECK_INTERVAL_MINUTES * 60)
        except KeyboardInterrupt:
            log.info("👋 Watcher stopped by user.")
            sys.exit(0)


def main():
    args = set(sys.argv[1:])
    if "--test" in args:
        mode_test()
    elif "--single-check" in args:
        mode_single_check()
    else:
        mode_loop()


if __name__ == "__main__":
    main()
