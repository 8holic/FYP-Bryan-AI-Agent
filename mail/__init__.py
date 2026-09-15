import base64
import datetime
import html
import http.server
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser

import llm

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_FILE = os.path.join(HERE, ".env")
SEEN_FILE = os.path.join(HERE, ".gmail_seen.json")
SUMMARY_FILE = os.path.join(HERE, ".mailbox_summary.md")

_tok = {"access": None, "exp": 0}
_summary_lock = threading.Lock()
_last_flush_date = None


def analyze(model, sender, subject, body):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M (%A)")
    prompt = (
        f"Current date and time: {now}\n"
        "Triage this email for Bryan. Decide if it is urgent, then summarize it.\n"
        "URGENT = needs immediate attention: a deadline within a day or two, security or account "
        "alerts, payment or billing problems, a time-sensitive request from a real person, or "
        "anything that loses money or an opportunity if missed.\n"
        "NON-URGENT = newsletters, promotions, receipts, FYI, social notifications, routine updates.\n\n"
        "Reply with JSON only: "
        '{"urgent": true or false, "summary": "3-5 short sentences covering what it is about, who it '
        'is from, and any deadline, date or amount of money mentioned."}\n\n'
        f"From: {sender}\nSubject: {subject}\n\n{body}"
    )
    raw = llm.chat(model, prompt, fmt="json")
    try:
        data = json.loads(raw)
        return bool(data.get("urgent")), str(data.get("summary", "")).strip()
    except (ValueError, AttributeError):
        return False, raw.strip()


def notify(env, sender, subject, summary, urgent=False):
    prefix = "URGENT - " if urgent else "Email: "
    text = f"{prefix}{subject}\nFrom: {sender}\n\n{summary}"
    llm.send(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], text)


def append_summary(sender, subject, summary):
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"## {stamp} | {sender} | {subject}\n{summary}\n\n"
    with _summary_lock:
        with open(SUMMARY_FILE, "a", encoding="utf-8") as f:
            f.write(entry)


def flush_summary(env):
    global _last_flush_date
    with _summary_lock:
        if not os.path.exists(SUMMARY_FILE):
            return False
        with open(SUMMARY_FILE, encoding="utf-8") as f:
            text = f.read().strip()
        if not text:
            return False
        # ponytail: one Telegram message, 4096-char cap; chunk or sendDocument if the day's summary exceeds it
        llm.send(env["TELEGRAM_TOKEN"], env["TELEGRAM_CHAT_ID"], text)
        os.remove(SUMMARY_FILE)
        _last_flush_date = datetime.date.today()
        return True


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
    with urllib.request.urlopen(req, timeout=600) as resp:
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


def image_parts(payload):
    if not payload:
        return []
    out = []
    if payload.get("mimeType", "").startswith("image/") and payload.get("body", {}).get("data"):
        out.append(payload["body"]["data"])
    for part in payload.get("parts", []) or []:
        out.extend(image_parts(part))
    return out


_ocr = None


def ocr_images(payload):
    global _ocr
    data_list = image_parts(payload)
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
            result, _ = _ocr(base64.urlsafe_b64decode(data))
        except Exception as e:
            print(f"OCR error: {e}")
            continue
        if result:
            lines.extend(line[1] for line in result)
    return "\n".join(lines)


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
        print(f"Reading email from {sender}: {subject}")
        body = mime_text(payload)
        image_text = ocr_images(payload)
        if image_text:
            body += "\n\n[Image text]\n" + image_text
        urgent, summary = analyze(model, sender, subject, body[:4000])
        print(f"{'URGENT' if urgent else 'Mail'} from {sender}: {subject}")
        if urgent:
            notify(env, sender, subject, summary, urgent=True)
        else:
            append_summary(sender, subject, summary)
        seen.add(mid)
        seen = save_seen(seen)


def ensure_email_env():
    env = llm.env_items(ENV_FILE)
    if not env.get("GMAIL_CLIENT_ID"):
        print("Add GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET to .env, then run again.")
        return
    if not env.get("GMAIL_CLIENT_SECRET"):
        print("Add GMAIL_CLIENT_SECRET=<your client secret> to .env, then run again.")
        return
    if not env.get("GMAIL_REFRESH_TOKEN"):
        print("Need to authorize the Gmail account once.")
        env["GMAIL_REFRESH_TOKEN"] = consent(env)
        llm.write_env(ENV_FILE, env)
        print(f"Saved to {ENV_FILE}")


def poll_gmail_once(env, model):
    try:
        poll_once_gmail(env, model)
    except Exception as e:
        print(f"Poll error: {e}")


def run_poller():
    global _last_flush_date
    warned = False
    needed = ("GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN",
              "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID")
    while True:
        env = llm.env_items(ENV_FILE)
        llm.load_env_file(ENV_FILE)
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
        poll_gmail_once(env, model)
        today = datetime.date.today()
        if datetime.datetime.now().hour >= 8 and _last_flush_date != today:
            try:
                flush_summary(env)
            except Exception as e:
                print(f"Summary flush error: {e}")
            _last_flush_date = today
        time.sleep(int(env.get("EMAIL_POLL_SECONDS", "60")))
