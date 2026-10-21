import base64
import datetime
import email
import getpass
import html
import imaplib
import io
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
SUMMARY_FILE = os.path.join(HERE, ".mailbox_summary.md")

_summary_lock = threading.Lock()
_last_flush_date = None
MARK_BATCH = 10


def _log(msg):
    print(f"{datetime.datetime.now().strftime('%H:%M:%S')} {msg}", flush=True)


def classify(model, sender, subject, body, date=""):
    llm.wait_for_user_idle()
    now = datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z (%A)")
    prompt = (
        f"Current date and time: {now}\n"
        f"Email received: {date}\n"
        "Decide two things about this email for Bryan.\n\n"
        "Compare the current date and time with the email's received date and with any date, "
        "time, or deadline it mentions.\n\n"
        '"advertisement": true if the email is promoting an event, product, sale, or newsletter.\n'
        '"urgent": true ONLY if Bryan must personally act within the next 48 hours and missing '
        "it has a concrete cost: a hard deadline he committed to, a payment or billing problem, "
        "a security or account alert, or a direct request from a real person waiting for his "
        "reply. A near date alone does not make an email urgent. Optional lectures, talks, "
        "seminars, webinars, info sessions, event invitations, surveys, sign-ups, and automated "
        "reminders are NOT urgent, even if they are upcoming, today, or use words like 'urgent' "
        "or 'last chance'. If the deadline or event has already passed, it is not urgent. "
        "An advertisement is never urgent.\n\n"
        'Reply with JSON only: {"advertisement": true or false, "urgent": true or false}\n\n'
        f"From: {sender}\nSubject: {subject}\n\n{body}"
    )
    raw = llm.chat(model, prompt, fmt="json", think=False)
    try:
        data = json.loads(raw)
        advertisement = bool(data.get("advertisement"))
        urgent = bool(data.get("urgent")) and not advertisement
    except (ValueError, TypeError, AttributeError):
        return {"advertisement": False, "urgent": False}
    return {
        "advertisement": advertisement,
        "urgent": urgent,
    }


def summarize(model, sender, subject, body):
    llm.wait_for_user_idle()
    prompt = (
        "Summarize this email in 3-5 short sentences. Include what it is about and who it is from. "
        "Mention a deadline, date, or amount of money only if the email actually states one. "
        "Never say that no deadline, date, or amount was mentioned.\n\n"
        f"From: {sender}\nSubject: {subject}\n\n{body}"
    )
    return llm.chat(model, prompt, think=False)


def vision_read(model, sender, subject, images):
    prompt = (
        "Read the images from this email. Transcribe all visible text exactly, and describe "
        "the important content: names, dates, deadlines, amounts, invoice or receipt details, "
        "or what a screenshot shows. Be factual and concise.\n\n"
        f"From: {sender}\nSubject: {subject}"
    )
    return llm.chat(model, prompt, images=images, think=False)


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
    return llm.chat(model, prompt, think=False)


def flush_summary(env, model):
    global _last_flush_date
    with _summary_lock:
        if not os.path.exists(SUMMARY_FILE):
            _log("digest: queue empty")
            return False
        with open(SUMMARY_FILE, encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            _log("digest: queue empty")
            return False
        # ponytail: one Telegram message, 4096-char cap; chunk or sendDocument if the digest exceeds it
        _log(f"digest: summarizing {len(text)} chars of notes")
        llm.send(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], digest(model, text))
        os.remove(SUMMARY_FILE)
        _last_flush_date = datetime.date.today()
        _log("digest: sent")
        return True


IMAP_HOST_DEFAULT = "imap.gmail.com"
IMAP_PORT_DEFAULT = 993


class LoginError(RuntimeError):
    pass


def _credentials(env):
    address = (env.get("IMAP_ADDRESS") or env.get("GMAIL_ADDRESS") or "").strip()
    password = (env.get("IMAP_PASSWORD") or env.get("GMAIL_APP_PASSWORD") or "")
    return address, password.replace(" ", "").strip()


def _imap_connect(env):
    host = env.get("IMAP_HOST", "").strip() or IMAP_HOST_DEFAULT
    port = int(env.get("IMAP_PORT", "").strip() or IMAP_PORT_DEFAULT)
    address, password = _credentials(env)
    detail = (f"IMAP_ADDRESS={len(address)} chars (has '@': {'@' in address}), "
              f"IMAP_PASSWORD={len(password)} chars after removing spaces")
    conn = imaplib.IMAP4_SSL(host, port, timeout=120)
    try:
        try:
            conn.login(address, password)
        except imaplib.IMAP4.abort:
            raise
        except imaplib.IMAP4.error as e:
            raise LoginError(
                f"{host} rejected the login: {e}. {detail}. "
                "Check the address, the password/app password, and that IMAP is enabled.") from e
    except Exception:
        try:
            conn.shutdown()
        except Exception:
            pass
        raise
    return conn


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
    return ocr_bytes(image_parts(msg))


def ocr_bytes(data_list):
    global _ocr
    if not data_list:
        return ""
    try:
        import numpy as np
        from PIL import Image
        from rapidocr_onnxruntime import RapidOCR
    except ImportError:
        print("rapidocr-onnxruntime not installed, skipping image OCR")
        return ""
    if _ocr is None:
        _ocr = RapidOCR()
    lines = []
    for data in data_list:
        try:
            img = Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB")
            result, _ = _ocr(np.array(img))
        except Exception as e:
            print(f"OCR error: {e}")
            continue
        if result:
            lines.extend(line[1] for line in result)
    return "\n".join(lines)


def _mark_ai_read(conn, uids, host):
    seq = b",".join(uids)
    try:
        typ, data = conn.uid("STORE", seq, "+FLAGS", r"(\Seen)")
        if typ != "OK":
            _log(f"Mark-read error: {data}")
            return False
    except Exception as e:
        _log(f"Mark-read error: {e}")
        return False
    try:
        if host.lower().endswith("gmail.com"):
            conn.uid("STORE", seq, "+X-GM-LABELS", '"AI Read"')
        else:
            conn.uid("STORE", seq, "+FLAGS", "(AIRead)")
    except Exception as e:
        _log(f"Tag error: {e}")
    return True


def poll_once_gmail(env, model):
    host = env.get("IMAP_HOST", "").strip() or IMAP_HOST_DEFAULT
    t_start = time.monotonic()
    login_s = select_s = search_s = loop_s = 0.0
    unread = skipped = processed = urgent = 0
    conn = _imap_connect(env)
    login_s = time.monotonic() - t_start
    try:
        t = time.monotonic()
        conn.select("INBOX")
        select_s = time.monotonic() - t
        _, data = conn.response("UIDVALIDITY")
        validity = data[0].decode() if data and data[0] else "0"
        t = time.monotonic()
        typ, data = conn.uid("SEARCH", None, "UNSEEN")
        search_s = time.monotonic() - t
        if typ != "OK":
            raise RuntimeError(f"IMAP search failed: {data}")
        uids = data[0].split() if data and data[0] else []
        unread = len(uids)
        t = time.monotonic()
        known = store.known()
        pending = []
        for uid in reversed(uids):
            mid = f"uid:{validity}:{uid.decode()}"
            if mid in known:
                skipped += 1
                continue
            typ, fetched = conn.uid("FETCH", uid, "(BODY.PEEK[])")
            if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                _log(f"Fetch failed for {mid}")
                continue
            msg = email.message_from_bytes(fetched[0][1], policy=policy.default)
            sender = str(msg.get("From", ""))
            subject = str(msg.get("Subject", ""))
            date = str(msg.get("Date", ""))
            _log(f"Reading email from {sender}: {subject}")
            body = message_text(msg)[:4000]
            image_text = ocr_images(msg)
            if image_text:
                body += "\n\n[Image text]\n" + image_text
            try:
                result = classify(model, sender, subject, body, date)
            except Exception as e:
                _log(f"Classify failed for {mid} ({sender}): {e}")
                raise
            processed += 1
            if result["urgent"]:
                urgent += 1
                _log(f"URGENT from {sender}: {subject}")
                notify(env, sender, subject, summarize(model, sender, subject, body))
            else:
                _log(f"Queued for digest: {subject}")
                append_summary(sender, subject, body)
            store.upsert(mid, sender, subject, date, body)
            pending.append(uid)
            if len(pending) >= MARK_BATCH:
                if not _mark_ai_read(conn, pending, host):
                    pending = []
                    break
                pending = []
        if pending:
            _mark_ai_read(conn, pending, host)
        loop_s = time.monotonic() - t
    finally:
        t = time.monotonic()
        try:
            conn.logout()
        except Exception:
            pass
        logout_s = time.monotonic() - t
        _log(f"poll: {unread} unread, {skipped} known, {processed} new, {urgent} urgent "
             f"({time.monotonic() - t_start:.1f}s: login {login_s:.1f}, select {select_s:.1f}, "
             f"search {search_s:.1f}, loop {loop_s:.1f}, logout {logout_s:.1f})")


def ensure_email_env():
    env = llm.env_items(ENV_FILE)
    changed = False
    address, password = _credentials(env)
    for key, prompt, have in (
        ("IMAP_ADDRESS", "Email address: ", address),
        ("IMAP_PASSWORD", "Email password or app password (no spaces): ", password),
    ):
        if have:
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
        return True
    except LoginError:
        raise
    except Exception as e:
        _log(f"poll error: {e}")
        return False


def run_poller():
    global _last_flush_date
    warned = False
    announced = False
    needed = ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
    while True:
        env = llm.env_items(ENV_FILE)
        llm.load_env_file(ENV_FILE)
        missing = [k for k in needed if not env.get(k)]
        address, password = _credentials(env)
        if not address:
            missing.append("IMAP_ADDRESS")
        if not password:
            missing.append("IMAP_PASSWORD")
        model = llm.OLLAMA_MODEL
        if not missing:
            try:
                available = llm.models()
            except Exception:
                available = []
            if model not in available and available:
                if model:
                    _log(f"model {model!r} is not installed; using {available[0]!r} instead")
                model = available[0]
        if missing or not model:
            if not warned:
                if missing:
                    _log(f"poller waiting for .env entries: {', '.join(missing)}")
                if not model:
                    _log("poller waiting for a model (start ollama or set OLLAMA_MODEL)")
                warned = True
            time.sleep(10)
            continue
        warned = False
        poll_seconds = int(env.get("EMAIL_POLL_SECONDS", "300"))
        host = env.get("IMAP_HOST", "").strip() or IMAP_HOST_DEFAULT
        if not announced:
            _log(f"poller ready: {host}, model {model}, every {poll_seconds}s, "
                 f"{store.count()} messages in db")
            announced = True
        try:
            poll_gmail_once(env, model)
        except LoginError as e:
            _log(f"login failed: {e}")
            _log("stopping")
            os._exit(1)
        today = datetime.date.today()
        if datetime.datetime.now().hour >= 8 and _last_flush_date != today:
            try:
                llm.wait_for_user_idle()
                flush_summary(env, model)
            except Exception as e:
                _log(f"summary flush error: {e}")
            _last_flush_date = today
        _log(f"next poll in {poll_seconds}s")
        time.sleep(poll_seconds)
