import base64
import datetime
import email as email_module
import getpass
import html
import http.server
import imaplib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from email.header import decode_header

import llm

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(HERE, ".env")
SEEN_FILE = os.path.join(HERE, ".gmail_seen.json")

_tok = {"access": None, "exp": 0}


def decode_text(value):
    if not value:
        return ""
    parts = decode_header(value)
    out = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def decode_payload(part):
    payload = part.get_payload(decode=True)
    if not payload:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def body_of(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                text = decode_payload(part)
                if text:
                    return text
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                text = decode_payload(part)
                if text:
                    return html.unescape(re.sub(r"<[^>]+>", " ", text))
    return decode_payload(msg)


def summarize(model, sender, subject, body):
    prompt = (
        "Summarize the following email in 3-5 short sentences. Include what it is about, "
        "who it is from, and any deadline, date, or amount of money mentioned.\n\n"
        f"From: {sender}\nSubject: {subject}\n\n{body}"
    )
    return llm.chat(model, prompt, system=llm.SYSTEM_PROMPT or llm.DEFAULT_SYSTEM_PROMPT)


def notify(env, sender, subject, summary):
    text = f"Email: {subject}\nFrom: {sender}\n\n{summary}"
    llm.send(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], text)


def connect(env):
    mail = imaplib.IMAP4_SSL(env["EMAIL_HOST"])
    mail.login(env["EMAIL_USER"], env["EMAIL_PASSWORD"])
    mail.select(env.get("EMAIL_MAILBOX", "INBOX"))
    return mail


def poll_once_imap(env, mail, model):
    typ, data = mail.search(None, "UNSEEN")
    if typ != "OK":
        return
    for num in data[0].split():
        typ, msg_data = mail.fetch(num, "(RFC822)")
        if typ != "OK":
            continue
        msg = email_module.message_from_bytes(msg_data[0][1])
        sender = decode_text(msg.get("From"))
        subject = decode_text(msg.get("Subject"))
        body = body_of(msg)[:4000]
        print(f"New email from {sender}: {subject}")
        summary = summarize(model, sender, subject, body)
        print(f"> {summary}")
        notify(env, sender, subject, summary)
        mail.store(num, "+FLAGS", "\\Seen")


def _oauth_token(params):
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token", data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token exchange failed ({e.code}): {e.read().decode()}")


def consent(env):
    client_id = env["GMAIL_CLIENT_ID"]
    client_secret = env["GMAIL_CLIENT_SECRET"]
    holder = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in query:
                holder["code"] = query["code"][0]
                body = b"Authorized. You can close this window."
            elif "error" in query:
                holder["error"] = query["error"][0]
                body = b"Authorization failed. Close and try again."
            else:
                body = b"No code received."
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    redirect = f"http://127.0.0.1:{port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/gmail.readonly",
        "access_type": "offline",
        "prompt": "select_account consent",
    })
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
    print("Opening browser for Google authorization...")
    webbrowser.open(url)
    while "code" not in holder and "error" not in holder:
        time.sleep(0.5)
    server.shutdown()
    if "error" in holder:
        raise RuntimeError(f"Google authorization failed: {holder['error']}")
    result = _oauth_token({
        "code": holder["code"],
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": redirect,
        "grant_type": "authorization_code",
    })
    return result["refresh_token"]


def access_token(env, force=False):
    if not force and _tok["access"] and time.time() < _tok["exp"]:
        return _tok["access"]
    result = _oauth_token({
        "grant_type": "refresh_token",
        "client_id": env["GMAIL_CLIENT_ID"],
        "client_secret": env["GMAIL_CLIENT_SECRET"],
        "refresh_token": env["GMAIL_REFRESH_TOKEN"],
    })
    _tok["access"] = result["access_token"]
    _tok["exp"] = time.time() + int(result.get("expires_in", 3600)) - 60
    return _tok["access"]


def gmail_get(env, path):
    try:
        access = access_token(env)
        return _gmail_get(access, path)
    except urllib.error.HTTPError as e:
        if e.code != 401:
            raise
    access = access_token(env, force=True)
    return _gmail_get(access, path)


def _gmail_get(access, path):
    req = urllib.request.Request(
        f"https://gmail.googleapis.com/gmail/v1/users/me{path}",
        headers={"Authorization": f"Bearer {access}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


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


def mime_text(payload):
    if not payload:
        return ""
    if payload.get("body", {}).get("data"):
        try:
            data = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", "replace")
        except Exception:
            data = ""
        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            return data
        if mime == "text/html":
            return html.unescape(re.sub(r"<[^>]+>", " ", data))
    for part in payload.get("parts", []) or []:
        text = mime_text(part)
        if text:
            return text
    return ""


def poll_once_gmail(env, model):
    seen = load_seen()
    start = (datetime.date.today() - datetime.timedelta(days=1)).strftime("%Y/%m/%d")
    data = gmail_get(env, f"/messages?q=is:unread%20-category:promotions%20after:{start}&maxResults=500")
    for msg in data.get("messages", []):
        mid = msg["id"]
        if mid in seen:
            continue
        full = gmail_get(env, f"/messages/{mid}?format=full")
        payload = full.get("payload", {})
        headers = {h.get("name", "").lower(): h.get("value", "")
                   for h in payload.get("headers", [])}
        sender = headers.get("from", "")
        subject = headers.get("subject", "")
        body = mime_text(payload)[:4000]
        print(f"New email from {sender}: {subject}")
        summary = summarize(model, sender, subject, body)
        print(f"> {summary}")
        notify(env, sender, subject, summary)
        seen.add(mid)
        seen = save_seen(seen)


def ensure_email_env():
    env = llm.env_items(ENV_FILE)
    changed = False
    if env.get("GMAIL_CLIENT_ID"):
        if not env.get("GMAIL_CLIENT_SECRET"):
            print("Add GMAIL_CLIENT_SECRET=<your client secret> to .env, then run again.")
            return
        if not env.get("GMAIL_REFRESH_TOKEN"):
            print("Need to authorize the Gmail account once.")
            env["GMAIL_REFRESH_TOKEN"] = consent(env)
            changed = True
        if not env.get("GMAIL_REFRESH_TOKEN"):
            print("Need to authorize the Gmail account once.")
            env["GMAIL_REFRESH_TOKEN"] = consent(env)
            changed = True
    else:
        for key, prompt, secret in (
            ("EMAIL_HOST", "Email IMAP server (e.g. imap.gmail.com or outlook.office365.com): ", False),
            ("EMAIL_USER", "Email address: ", False),
            ("EMAIL_PASSWORD", "Email app password: ", True),
        ):
            if not env.get(key):
                value = getpass.getpass(prompt).strip() if secret else input(prompt).strip()
                env[key] = value
                changed = True
    if "TELEGRAM_CHAT_ID" not in env or not env.get("TELEGRAM_CHAT_ID"):
        value = input("Your Telegram chat id (Enter to skip, auto-saved on first message): ").strip()
        if value:
            env["TELEGRAM_CHAT_ID"] = value
            changed = True
    if changed:
        llm.write_env(ENV_FILE, env)
        print(f"Saved to {ENV_FILE}")


def poll_gmail_once(env, model):
    try:
        poll_once_gmail(env, model)
    except Exception as e:
        print(f"Poll error: {e}")


def run_poller():
    warned = False
    while True:
        env = llm.env_items(ENV_FILE)
        llm.load_env_file(ENV_FILE)
        if env.get("GMAIL_CLIENT_ID"):
            needed = ("GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
                      "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
            poller = poll_gmail_once
        else:
            needed = ("EMAIL_HOST", "EMAIL_USER", "EMAIL_PASSWORD",
                      "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
            poller = poll_imap_once
        missing = [k for k in needed if not env.get(k)]
        model = llm.OLLAMA_MODEL
        if not missing and not model:
            try:
                available = llm.models()
                model = available[0] if available else ""
            except Exception:
                model = ""
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
        poller(env, model)
        time.sleep(int(env.get("EMAIL_POLL_SECONDS", "60")))


def poll_imap_once(env, model):
    try:
        mail = connect(env)
        poll_once_imap(env, mail, model)
        mail.logout()
    except Exception as e:
        print(f"Poll error: {e}")
