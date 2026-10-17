import base64
import datetime
import email
import getpass
import html
import imaplib
import json
import os
import re
import threading
import time
from email import policy

import llm
from . import store

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(HERE, ".env")
SEEN_FILE = os.path.join(HERE, ".gmail_seen.json")
SUMMARY_FILE = os.path.join(HERE, ".mailbox_summary.md")

_summary_lock = threading.Lock()
_last_flush_date = None


def classify(model, sender, subject, body):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    prompt = (
        f"Current date and time: {now}\n"
        "Is this email urgent for Bryan?\n"
        "URGENT = needs immediate attention: a deadline within a day or two, security or account "
        "alerts, payment or billing problems, a time-sensitive request from a real person, or "
        "anything that loses money or an opportunity if missed.\n"
        "NON-URGENT = newsletters, promotions, receipts, FYI, social notifications, routine updates.\n\n"
        'Reply with JSON only: {"urgent": true or false}\n\n'
        f"From: {sender}\nSubject: {subject}\n\n{body}"
    )
    raw = llm.chat(model, prompt, fmt="json")
    try:
        return bool(json.loads(raw).get("urgent"))
    except (ValueError, AttributeError):
        return False


def summarize(model, sender, subject, body):
    prompt = (
        "Summarize this email in 3-5 short sentences. Include what it is about and who it is from. "
        "Mention a deadline, date, or amount of money only if the email actually states one. "
        "Never say that no deadline, date, or amount was mentioned.\n\n"
        f"From: {sender}\nSubject: {subject}\n\n{body}"
    )
    return llm.chat(model, prompt)


def vision_read(model, sender, subject, images):
    prompt = (
        "Read the images from this email. Transcribe all visible text exactly, and describe "
        "the important content: names, dates, deadlines, amounts, invoice or receipt details, "
        "or what a screenshot shows. Be factual and concise.\n\n"
        f"From: {sender}\nSubject: {subject}"
    )
    return llm.chat(model, prompt, images=images)


def notify(env, sender, subject, summary):
    llm.send(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"],
             f"URGENT - {subject}\nFrom: {sender}\n\n{summary}")


def append_summary(sender, subject, body):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    snippet = " ".join(body.split())[:400]
    entry = f"## {stamp} | {sender} | {subject}\n{snippet}\n\n"
    with _summary_lock:
        with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)


def digest(model, text):
    prompt = (
        "Below are notes on recently received emails. Write a short digest that groups related "
        "emails by topic. If several emails are about the same thing, combine them into one line "
        "and say how many there were (for example: '4 emails about an economic study'). Do not "
        "list every email individually. Note anything time-sensitive and whether it has already "
        "passed. Keep it to a few lines.\n\n" + text[:12000]
    )
    return llm.chat(model, prompt)


def flush_summary(env, model):
    global _last_flush_date
    with _summary_lock:
        if not os.path.exists(SUMMARY_FILE):
            return False
        with open(SUMMARY_FILE, encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            return False
        # ponytail: one Telegram message, 4096-char cap; chunk or sendDocument if the digest exceeds it
        llm.send(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], digest(model, text))
        os.remove(SUMMARY_FILE)
        _last_flush_date = datetime.date.today()
        return True


IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
IMAP_QUERY = "is:unread -category:promotions newer_than:1d"


class LoginError(RuntimeError):
    pass


def _imap_connect(env):
    address = env.get("GMAIL_ADDRESS", "").strip()
    password = env.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    detail = (f"GMAIL_ADDRESS={len(address)} chars (has '@': {'@' in address}), "
              f"GMAIL_APP_PASSWORD={len(password)} chars after removing spaces")
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=120)
    try:
        try:
            conn.login(address, password)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error as e:
            raise LoginError(
                f"Gmail rejected the login: {e}. {detail}. "
                "Google app passwords are 16 characters and require 2-Step Verification.") from e
    except Exception:
        try:
            conn.shutdown()
        except Exception:
            pass
        raise
    return conn


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    if len(seen) > 2000:
        seen = set(sorted(seen)[-2000:])
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f)
    return seen


def message_text(msg):
    plain, rich = [], []
    for part in msg.walk():
        if part.get_content_maintype() == "multipart" or part.get_filename():
            continue
        payload = part.get_payload(decode=True)
        if not payload:
            continue
        try:
            text = payload.decode(part.get_content_charset() or "utf-8", "replace")
        except LookupError:
            text = payload.decode("utf-8", "replace")
        ctype = part.get_content_type()
        if ctype == "text/plain":
            plain.append(text)
        elif ctype == "text/html":
            rich.append(html.unescape(re.sub(r"<[^>]+>", " ", text)))
    return "\n".join(plain or rich)


def image_parts(msg):
    out = []
    for part in msg.walk():
        if part.get_content_maintype() == "image":
            data = part.get_payload(decode=True)
            if data:
                out.append(base64.b64encode(data).decode())
    return out


_ocr = None


def ocr_images(msg):
    global _ocr
    data_list = image_parts(msg)
    if not data_list:
        return ""
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print("rapidocr-onnxruntime not installed, skipping image OCR")
        return ""
    if _ocr is None:
        _ocr = RapidOCR()
    lines = []
    for data in data_list:
        try:
            result, _ = _ocr(base64.b64decode(data))
        except Exception as e:
            print(f"OCR error: {e}")
            continue
        if result:
            lines.extend(line[1] for line in result)
    return "\n".join(lines)


def poll_once_gmail(env, model):
    seen = load_seen()
    conn = _imap_connect(env)
    try:
        conn.select("INBOX", readonly=True)
        _, data = conn.response("UIDVALIDITY")
        validity = data[0].decode() if data and data[0] else "0"
        typ, data = conn.uid("SEARCH", None, "X-GM-RAW", f'"{IMAP_QUERY}"')
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {data}")
        for uid in (data[0].split() if data and data[0] else []):
            mid = f"uid:{validity}:{uid.decode()}"
            if mid in seen:
                continue
            typ, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                print(f"Fetch failed for {mid}")
                continue
            msg = email.message_from_bytes(fetched[0][1], policy=policy.default)
            sender = str(msg.get("From", ""))
            subject = str(msg.get("Subject", ""))
            date = str(msg.get("Date", ""))
            print(f"Reading email from {sender}: {subject}")
            body = message_text(msg)[:4000]
            image_text = ocr_images(msg)
            if image_text:
                body += "\n\n[Image text]\n" + image_text
            try:
                urgent = classify(model, sender, subject, body)
            except Exception as e:
                print(f"Classify failed for {mid} ({sender}): {e}")
                raise
            store.upsert(mid, sender, subject, date, body, urgent)
            if urgent:
                print(f"URGENT from {sender}: {subject}")
                notify(env, sender, subject, summarize(model, sender, subject, body))
            else:
                print(f"Queued for digest: {subject}")
                append_summary(sender, subject, body)
            seen.add(mid)
            seen = save_seen(seen)
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def ensure_email_env():
    env = llm.env_items(ENV_FILE)
    changed = False
    for key, prompt in (
        ("GMAIL_ADDRESS", "Gmail address: "),
        ("GMAIL_APP_PASSWORD", "Gmail app password (16 characters, no spaces): "),
    ):
        if env.get(key):
            continue
        try:
            value = getpass.getpass(prompt) if key.endswith("PASSWORD") else input(prompt)
        except EOFError:
            value = ""
        value = value.strip()
        if not value:
            print(f"Add {key}=... to .env, then run again.")
            return
        env[key] = value
        changed = True
    if changed:
        llm.write_env(ENV_FILE, env)
        print(f"Saved to {ENV_FILE}")


def poll_gmail_once(env, model):
    try:
        poll_once_gmail(env, model)
    except LoginError:
        raise
    except Exception as e:
        print(f"Poll error: {e}")


def run_poller():
    global _last_flush_date
    warned = False
    needed = ("GMAIL_ADDRESS", "GMAIL_APP_PASSWORD",
              "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
    while True:
        env = llm.env_items(ENV_FILE)
        llm.load_env_file(ENV_FILE)
        missing = [k for k in needed if not env.get(k)]
        model = llm.OLLAMA_MODEL
        if not missing:
            try:
                available = llm.models()
            except Exception:
                available = []
            if model not in available and available:
                if model:
                    print(f"Model {model!r} is not installed; using {available[0]!r} instead")
                model = available[0]
        if missing or not model:
            if not warned:
                if missing:
                    print(f"Mail poller waiting for .env entries: {', '.join(missing)}")
                if not model:
                    print("Mail poller waiting for a model (start ollama or set OLLAMA_MODEL)")
                warned = True
            time.sleep(10)
            continue
        warned = False
        try:
            poll_gmail_once(env, model)
        except LoginError as e:
            print(f"Login failed: {e}", flush=True)
            print("Stopping.", flush=True)
            os._exit(1)
        today = datetime.date.today()
        if datetime.datetime.now().hour >= 8 and _last_flush_date != today:
            try:
                flush_summary(env, model)
            except Exception as e:
                print(f"Summary flush error: {e}")
            _last_flush_date = today
        time.sleep(int(env.get("EMAIL_POLL_SECONDS", "60")))
