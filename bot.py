"""
Amazon.in Deal Bot
==================
Monitors public Telegram deal channels, picks up fresh Amazon India deals,
rebuilds them with YOUR affiliate tag and posts them to your channel.

Free forever: runs on GitHub Actions (cron) + Telegram Bot API.
Local test:   python bot.py --dry-run
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE = Path(__file__).resolve().parent
CONFIG = json.loads((BASE / "config.json").read_text(encoding="utf-8"))
STATE_FILE = BASE / "posted.json"

BOT_TOKEN = os.environ.get("BOT_TOKEN", "").strip()
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
AMAZON_TAG = os.environ.get("AMAZON_TAG", "").strip()
DRY_RUN = "--dry-run" in sys.argv or os.environ.get("DRY_RUN") == "1"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )
}

# /dp/B0XXXXXXX, /gp/product/..., /d/B0XXXXXXX, ?asin=B0XXXXXXX ...
ASIN_PATTERNS = [
    re.compile(r"/(?:dp|gp/product|gp/aw/d|product|d)/([A-Z0-9]{10})(?:[/?#]|$)"),
    re.compile(r"[?&]asin=([A-Z0-9]{10})"),
]
BARE_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+")
PHOTO_URL_RE = re.compile(r"url\('([^']+)'\)")
HANDLE_RE = re.compile(r"\B@[A-Za-z0-9_]{4,}")


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# --------------------------------------------------------------------------- #
# Fetching & parsing source channels
# --------------------------------------------------------------------------- #

def fetch_channel_messages(session: requests.Session, channel: str) -> list[dict]:
    """Read the public web preview of a Telegram channel and return its
    recent messages (text, photo url, timestamp) newest-first."""
    url = f"https://t.me/s/{channel}"
    try:
        resp = session.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as exc:
        log(f"  ! could not fetch @{channel}: {exc}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    messages = []
    for msg in soup.select("div.tgme_widget_message"):
        text_div = msg.select_one(".tgme_widget_message_text")
        if text_div is None:
            continue  # service / photo-only messages
        time_el = msg.select_one("time")
        posted_at = None
        if time_el and time_el.get("datetime"):
            try:
                posted_at = datetime.fromisoformat(time_el["datetime"])
            except ValueError:
                pass

        photo_wrap = msg.select_one("a.tgme_widget_message_photo_wrap")
        photo_url = None
        if photo_wrap and photo_wrap.get("style"):
            match = PHOTO_URL_RE.search(photo_wrap["style"])
            if match:
                photo_url = match.group(1)

        messages.append(
            {
                "channel": channel,
                "text_html": str(text_div),
                "text": text_div.get_text("\n", strip=True),
                "hrefs": [a.get("href", "") for a in text_div.find_all("a")],
                "photo": photo_url,
                "posted_at": posted_at,
            }
        )
    messages.reverse()  # t.me lists oldest first; we want newest first
    return messages


# --------------------------------------------------------------------------- #
# Amazon link handling
# --------------------------------------------------------------------------- #

def asin_from_url(url: str) -> str | None:
    if "amazon." not in urlparse(url).netloc:
        return None
    for pattern in ASIN_PATTERNS:
        match = pattern.search(url)
        if match:
            return match.group(1)
    return None


def resolve_shortlink(session: requests.Session, url: str) -> str:
    """Follow redirects of an amzn.to-style short link to its final URL."""
    try:
        resp = session.get(
            url, headers=HEADERS, timeout=15, allow_redirects=True, stream=True
        )
        final_url = resp.url
        resp.close()
        return final_url
    except requests.RequestException:
        return url


def extract_asin(session: requests.Session, message: dict) -> str | None:
    """Find the first Amazon ASIN in a message (resolving short links)."""
    candidates = [h for h in message["hrefs"] if h.startswith("http")]
    candidates += [u for u in BARE_URL_RE.findall(message["text"])]
    short_domains = tuple(CONFIG["shortlink_domains"])

    seen = set()
    for url in candidates:
        url = url.rstrip(".,)")
        if url in seen:
            continue
        seen.add(url)
        asin = asin_from_url(url)
        if asin:
            return asin
        if urlparse(url).netloc.endswith(short_domains):
            asin = asin_from_url(resolve_shortlink(session, url))
            if asin:
                return asin
    return None


def affiliate_link(asin: str) -> str:
    return f"https://www.amazon.in/dp/{asin}?tag={AMAZON_TAG}"


def shorten_url(session: requests.Session, url: str) -> str:
    """Shorten via TinyURL's free endpoint (Amazon's own amzn.to is
    SiteStripe-only and can't be created by bots). Falls back to the
    full URL, which still carries the affiliate tag."""
    try:
        resp = session.get(
            "https://tinyurl.com/api-create.php",
            params={"url": url},
            headers=HEADERS,
            timeout=15,
        )
        short = resp.text.strip()
        if short.startswith("http"):
            return short
    except requests.RequestException:
        pass
    return url


# --------------------------------------------------------------------------- #
# Filtering & formatting
# --------------------------------------------------------------------------- #

def is_too_old(message: dict, max_age_hours: int) -> bool:
    if message["posted_at"] is None:
        return False  # no timestamp visible -> keep it
    age = datetime.now(timezone.utc) - message["posted_at"]
    return age.total_seconds() > max_age_hours * 3600


def is_blocked(text: str) -> bool:
    lowered = text.lower()
    return any(kw.lower() in lowered for kw in CONFIG["block_keywords"])


def clean_text(text: str) -> str:
    text = BARE_URL_RE.sub("", text)
    text = HANDLE_RE.sub("", text)
    lines = [line.strip() for line in text.splitlines()]
    lines = [ln for ln in lines if ln and not ln.startswith("#")]
    # drop lines with no real content (lone emojis, arrows, "via" leftovers)
    lines = [ln for ln in lines if re.search(r"[A-Za-z0-9₹]", ln)]
    lines = [ln for ln in lines if ln.lower() not in {"via", "buy", "link", "here"}]
    return "\n".join(lines)[:900]


def build_caption(message: dict, asin: str, session: requests.Session) -> str:
    body = clean_text(message["text"])
    link = affiliate_link(asin)
    if CONFIG.get("short_links"):
        link = shorten_url(session, link)
    caption = (
        f"🔥 Amazon Deal 🔥\n\n"
        f"{body}\n\n"
        f"🛒 Buy Now 👉 {link}\n\n"
        f"📎 {CONFIG['disclosure']}"
    )
    return caption[:1024]


# --------------------------------------------------------------------------- #
# Posting
# --------------------------------------------------------------------------- #

def telegram_post(caption: str, photo_url: str | None) -> bool:
    api = f"https://api.telegram.org/bot{BOT_TOKEN}"
    for attempt in range(2):
        try:
            if photo_url:
                resp = requests.post(
                    f"{api}/sendPhoto",
                    json={"chat_id": CHANNEL_ID, "photo": photo_url, "caption": caption},
                    timeout=30,
                )
                if resp.json().get("ok"):
                    return True
                log(f"  ! sendPhoto failed: {resp.json().get('description')}")
            resp = requests.post(
                f"{api}/sendMessage",
                json={"chat_id": CHANNEL_ID, "text": caption[:4000]},
                timeout=30,
            )
            if resp.json().get("ok"):
                return True
            log(f"  ! sendMessage failed: {resp.json().get('description')}")
        except requests.RequestException as exc:
            log(f"  ! network error: {exc}")
        time.sleep(3)
    return False


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> int:
    if not DRY_RUN and not (BOT_TOKEN and CHANNEL_ID and AMAZON_TAG):
        log("Missing BOT_TOKEN / CHANNEL_ID / AMAZON_TAG environment variables.")
        return 1

    state = load_state()
    now = time.time()
    dedup_seconds = CONFIG["dedup_days"] * 86400
    fresh_cutoff = now - dedup_seconds

    session = requests.Session()
    candidates = []
    for channel in CONFIG["sources"]:
        log(f"Checking @{channel} ...")
        for msg in fetch_channel_messages(session, channel):
            if len(msg["text"]) < 20 or is_blocked(msg["text"]):
                continue
            if is_too_old(msg, CONFIG["max_deal_age_hours"]):
                continue
            asin = extract_asin(session, msg)
            if not asin:
                continue
            last_posted = state.get(asin, 0)
            if last_posted > fresh_cutoff:
                continue  # already posted recently
            candidates.append({"msg": msg, "asin": asin, "dup": last_posted > 0})
        time.sleep(1)

    # never-posted deals first, newest first within each group
    def sort_key(cand: dict):
        ts = cand["msg"]["posted_at"]
        return (cand["dup"], -(ts.timestamp() if ts else 0.0))

    candidates.sort(key=sort_key)

    if not candidates:
        log("No new deals found this run.")
        return 0

    posted = 0
    for cand in candidates:
        if posted >= CONFIG["max_posts_per_run"]:
            break
        msg, asin = cand["msg"], cand["asin"]
        caption = build_caption(msg, asin, session)
        log(f"Deal: {asin} (from @{msg['channel']})")
        if DRY_RUN:
            print("-" * 60)
            print(caption)
            print("-" * 60)
            state[asin] = now
            posted += 1
            continue
        if telegram_post(caption, msg["photo"]):
            state[asin] = now
            posted += 1
            save_state(state)
            log(f"  ✓ posted to channel ({posted}/{CONFIG['max_posts_per_run']})")
            time.sleep(3)

    save_state(state)
    log(f"Done. Posted {posted} deal(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
