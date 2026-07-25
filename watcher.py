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
import json
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
SENDER_EMAIL           = os.getenv("SENDER_EMAIL", "")
SENDER_APP_PASSWORD    = os.getenv("SENDER_APP_PASSWORD", "")
RECIPIENT_EMAILS       = [e.strip() for e in os.getenv("RECIPIENT_EMAILS", "").split(",") if e.strip()]
CHECK_INTERVAL_MINUTES = int(os.getenv("CHECK_INTERVAL_MINUTES", "30"))

BMS_URL       = "https://in.bookmyshow.com/cinemas/ahmedabad/pvr-palladium-mall-ahmedabad/buytickets/PPAM/20260801"
TARGET_MOVIE  = "odyssey"   # case-insensitive substring match
TARGET_FORMAT = "imax"      # must also appear near the movie name

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


# ─── Parsing helpers ──────────────────────────────────────────────────────────

def _extract_times_from_text(text: str) -> list[str]:
    """Pull HH:MM AM/PM tokens out of any text blob."""
    raw = re.findall(r"\b\d{1,2}:\d{2}\s*(?:AM|PM)\b", text, re.IGNORECASE)
    seen: set[str] = set()
    result: list[str] = []
    for t in raw:
        key = t.upper().replace(" ", "")
        if key not in seen:
            seen.add(key)
            result.append(t.upper())
    return result


def _parse_next_data(html: str) -> list[str] | None:
    """
    BMS is a Next.js app. It embeds all server-side data in a <script id="__NEXT_DATA__">
    tag as JSON. This is the most reliable source because it's present even when the
    rendered DOM is blocked or incomplete.
    Returns list of IMAX Odyssey show times, or None if the tag is missing.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        log.info("__NEXT_DATA__ not found in page.")
        return None

    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError:
        log.info("__NEXT_DATA__ JSON parse failed.")
        return None

    data_str = json.dumps(data).lower()
    if TARGET_MOVIE not in data_str or TARGET_FORMAT not in data_str:
        log.info("__NEXT_DATA__: Odyssey/IMAX not present in JSON.")
        return []  # Page loaded but movie not listed

    log.info("__NEXT_DATA__: Found Odyssey + IMAX — extracting times from JSON.")
    times = _extract_times_from_text(json.dumps(data))
    return times


def _parse_api_json(payload: dict | list) -> list[str]:
    """
    Parse an intercepted BMS API JSON response.
    Recursively searches for Odyssey + IMAX show times anywhere in the structure.
    """
    raw = json.dumps(payload)
    if TARGET_MOVIE not in raw.lower() or TARGET_FORMAT not in raw.lower():
        return []
    log.info("Intercepted API JSON contains Odyssey + IMAX — extracting times.")
    return _extract_times_from_text(raw)


def _parse_html_dom(html: str) -> list[str]:
    """
    Fallback: walk the rendered DOM looking for Odyssey rows with IMAX tags.
    Same as original approach but more lenient.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    page_text = soup.get_text(" ", strip=True)

    if TARGET_MOVIE not in page_text.lower() or TARGET_FORMAT not in page_text.lower():
        return []

    show_times: list[str] = []
    odyssey_nodes = soup.find_all(string=lambda t: t and TARGET_MOVIE in t.lower())

    for node in odyssey_nodes:
        ancestor = node.find_parent()
        for _ in range(14):
            if ancestor is None:
                break
            ancestor_text = ancestor.get_text(" ", strip=True)
            if TARGET_FORMAT in ancestor_text.lower():
                show_times.extend(_extract_times_from_text(ancestor_text))
                break
            ancestor = ancestor.find_parent()

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for t in show_times:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique


# ─── Scraping methods ─────────────────────────────────────────────────────────

# Shared browser-like headers for plain HTTP requests
_REQ_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/138.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-IN,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}


def _get_nextjs_build_id() -> str | None:
    """
    Fetch the BMS homepage and extract the Next.js buildId from __NEXT_DATA__.
    The homepage is far less likely to be blocked than the cinema page.
    """
    import requests
    try:
        resp = requests.get(
            "https://in.bookmyshow.com/",
            headers={**_REQ_HEADERS, "Accept": "text/html,*/*"},
            timeout=20,
        )
        data = _parse_next_data(resp.text)
        # We parsed the JSON — now pull buildId straight from the raw tag
        from bs4 import BeautifulSoup
        tag = BeautifulSoup(resp.text, "html.parser").find("script", id="__NEXT_DATA__")
        if tag and tag.string:
            build_id = json.loads(tag.string).get("buildId")
            if build_id:
                log.info(f"Got Next.js buildId: {build_id}")
                return build_id
    except Exception as e:
        log.info(f"buildId fetch failed: {e}")
    return None


def check_with_nextjs_api() -> list[str] | None:
    """
    BMS is a Next.js app. Every page's server-side data is also available as a
    JSON blob at:  /_next/data/{buildId}/{page path}.json

    This endpoint is NOT protected the same way as the HTML page, so it often
    works from datacenter IPs (GitHub Actions) when the HTML page returns 403.

    Returns None  → couldn't reach the endpoint (try next method).
    Returns []    → endpoint OK but Odyssey IMAX not listed yet.
    Returns [..] → found show times.
    """
    import requests

    build_id = _get_nextjs_build_id()
    if not build_id:
        log.info("Next.js API: could not get buildId — skipping.")
        return None

    # Construct the _next/data URL for the cinema page
    page_path = (
        "cinemas/ahmedabad/pvr-palladium-mall-ahmedabad"
        f"/buytickets/PPAM/20260801"
    )
    json_url = f"https://in.bookmyshow.com/_next/data/{build_id}/{page_path}.json"
    log.info(f"Trying Next.js JSON API: {json_url}")

    try:
        resp = requests.get(
            json_url,
            headers={
                **_REQ_HEADERS,
                "Accept": "application/json, */*",
                "Referer": BMS_URL,
                "x-nextjs-data": "1",
            },
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        raw = json.dumps(payload)
        log.info(f"Next.js API responded ({len(raw)} chars).")

        if TARGET_MOVIE not in raw.lower() or TARGET_FORMAT not in raw.lower():
            log.info("Next.js API: Odyssey/IMAX not in response — not listed yet.")
            return []

        times = _extract_times_from_text(raw)
        log.info(f"Next.js API: extracted {len(times)} show times.")
        return times

    except Exception as e:
        log.info(f"Next.js API failed: {e}")
        return None


def check_with_requests() -> list[str] | None:
    """
    Fast path: plain HTTP GET of the cinema HTML page.
    Returns None  → page needs JS rendering (fallback to Playwright).
    Returns []    → rendered but Odyssey IMAX not found.
    Returns [..] → found.
    """
    import requests

    resp = requests.get(
        BMS_URL,
        headers={**_REQ_HEADERS, "Referer": "https://in.bookmyshow.com/"},
        timeout=30,
    )
    resp.raise_for_status()

    # Try __NEXT_DATA__ first — present even in SSR responses
    nd = _parse_next_data(resp.text)
    if nd is not None:
        return nd

    # Check if the page contains useful rendered content
    body = resp.text.lower()
    page_seems_rendered = (
        "showtime" in body or "show-details" in body or "__movie-name" in body
    )
    if not page_seems_rendered:
        log.info("requests: page not fully rendered — will try Playwright.")
        return None

    return _parse_html_dom(resp.text)


def check_with_playwright() -> list[str]:
    """
    Full browser path with:
      1. playwright-stealth  → removes automation fingerprints
      2. API interception    → captures raw JSON from BMS internal XHR calls
      3. __NEXT_DATA__ parse → parses embedded Next.js data blob
      4. DOM parse           → last-resort HTML scraping
    """
    from playwright.sync_api import sync_playwright, Response

    # ── Stealth import (optional — graceful degradation if not installed) ──
    try:
        from playwright_stealth import stealth_sync
        _stealth_available = True
    except ImportError:
        _stealth_available = False
        log.info("playwright-stealth not installed — running without it.")

    intercepted_times: list[str] = []

    def on_response(response: Response) -> None:
        """Intercept BMS internal API calls and parse their JSON for show data."""
        url = response.url.lower()
        # Target XHR calls that are likely to contain showtime data
        relevant = (
            "showtime" in url or
            "show-time" in url or
            "showtimes" in url or
            "buytickets" in url or
            "availability" in url or
            "event" in url
        )
        if not relevant:
            return
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            payload = response.json()
            times = _parse_api_json(payload)
            if times:
                log.info(f"API intercept hit on: {response.url}")
                intercepted_times.extend(times)
        except Exception:
            pass

    log.info("Launching headless Chromium with stealth…")
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                # Remove common headless detection flags
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-accelerated-2d-canvas",
                "--disable-gpu",
                "--no-first-run",
                "--no-zygote",
                # Make browser look more like a real desktop Chrome
                "--window-size=1366,768",
                "--start-maximized",
            ],
        )
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/138.0.0.0 Safari/537.36"
            ),
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            viewport={"width": 1366, "height": 768},
            java_script_enabled=True,
            extra_http_headers={
                "Accept-Language": "en-IN,en;q=0.9,hi;q=0.8",
                "sec-ch-ua": '"Chromium";v="138", "Google Chrome";v="138", "Not-A.Brand";v="99"',
                "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": '"Windows"',
                "Upgrade-Insecure-Requests": "1",
            },
        )
        page = ctx.new_page()

        # Apply stealth patches before any navigation
        if _stealth_available:
            stealth_sync(page)
            log.info("Stealth mode active.")

        # Hook into network responses BEFORE navigating
        page.on("response", on_response)

        log.info(f"Navigating to: {BMS_URL}")
        try:
            page.goto(BMS_URL, wait_until="networkidle", timeout=90_000)
        except Exception as e:
            log.warning(f"networkidle timed out ({e}), continuing with domcontentloaded…")
            try:
                page.goto(BMS_URL, wait_until="domcontentloaded", timeout=60_000)
            except Exception:
                pass

        # Wait for movie listing selectors
        selectors = [
            "[class*='show-details']",
            "[class*='__movie-name']",
            "[class*='movie-name']",
            "[class*='showtime']",
            "[class*='ShowTimings']",
        ]
        for sel in selectors:
            try:
                page.wait_for_selector(sel, timeout=8_000)
                log.info(f"Found selector: {sel}")
                break
            except Exception:
                pass

        # Extra settle time for lazy content + intercepts to complete
        page.wait_for_timeout(4_000)

        html = page.content()
        browser.close()

    # ── Priority 1: API interception results ──────────────────────────────
    if intercepted_times:
        log.info(f"Using {len(intercepted_times)} times from intercepted API calls.")
        return _dedup(intercepted_times)

    # ── Priority 2: __NEXT_DATA__ JSON ────────────────────────────────────
    nd = _parse_next_data(html)
    if nd is not None:
        return nd

    # ── Priority 3: DOM HTML parsing ──────────────────────────────────────
    log.info("Falling back to DOM HTML parsing.")
    return _parse_html_dom(html)


def _dedup(times: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for t in times:
        k = t.upper().replace(" ", "")
        if k not in seen:
            seen.add(k)
            out.append(t)
    return out


def check_tickets() -> tuple[bool, list[str]]:
    """
    Multi-method check. Tries faster/lighter methods first, escalates to Playwright.
      1. Next.js JSON API  — no browser, works from datacenter IPs
      2. Plain HTTP GET    — fast, works when BMS SSRs the page
      3. Playwright        — full headless browser, last resort
    Returns (found: bool, show_times: list[str]).
    """
    log.info("🔍 Checking BMS for The Odyssey (IMAX) on Aug 1…")

    # ── Method 1: Next.js /_next/data JSON API ────────────────────────────
    try:
        result = check_with_nextjs_api()
        if result is not None:
            if result:
                log.info(f"✅ ODYSSEY IMAX FOUND via Next.js API! Times: {result}")
                return True, result
            else:
                log.info("❌ Odyssey IMAX not yet listed (Next.js API).")
                return False, []
    except Exception as e:
        log.info(f"Next.js API exception: {e}")

    # ── Method 2: Plain HTTP GET of cinema HTML page ──────────────────────
    try:
        result = check_with_requests()
        if result is not None:
            if result:
                log.info(f"✅ ODYSSEY IMAX FOUND via requests! Times: {result}")
                return True, result
            elif result == []:
                log.info("❌ Odyssey IMAX not yet listed (requests).")
                return False, []
    except Exception as e:
        log.info(f"requests path: {e}")

    # ── Method 3: Full Playwright browser ────────────────────────────────
    log.info("Escalating to Playwright (full headless browser)…")
    try:
        show_times = check_with_playwright()
    except Exception as pw_err:
        log.error(f"Playwright failed: {pw_err}")
        return False, []

    if show_times:
        log.info(f"✅ ODYSSEY IMAX FOUND via Playwright! Times: {show_times}")
        return True, show_times

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
    <div style="background:linear-gradient(135deg,#c0392b 0%,#8e0000 100%);padding:40px 30px;text-align:center">
      <div style="font-size:48px;margin-bottom:8px">🎬</div>
      <h1 style="margin:0;color:#fff;font-size:30px;letter-spacing:1px">THE ODYSSEY — IMAX</h1>
      <p style="margin:10px 0 0;color:#ffcdd2;font-size:15px">Tickets are LIVE! Don't miss out!</p>
    </div>
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
        log.error("❌ Gmail auth failed — check SENDER_APP_PASSWORD (must be an App Password, not your account password).")
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
    Exit 0 = tickets found + email sent.
    Exit 1 = not found yet (normal — not an error).
    Exit 2 = tickets found but email failed.
    """
    found, show_times = check_tickets()
    if found:
        ok = send_email(show_times)
        sys.exit(0 if ok else 2)
    else:
        sys.exit(1)


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
                log.info("🎉 Notification sent! Watcher keeps running for further updates.")
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
