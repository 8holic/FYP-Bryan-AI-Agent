import json
import os
import urllib.error
import urllib.request

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "")
OLLAMA_CONTEXT_LENGTH = 0
OLLAMA_VISION_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "")
DEFAULT_SYSTEM_PROMPT = (
    "You are Bryan's personal assistant bot running on Telegram, connected to his email. "
    "Your job is to triage his incoming mail: urgent messages are sent to him immediately, "
    "non-urgent ones are collected into a summary he can request by sending the word 'mailbox', "
    "or that is sent automatically every morning at 8am. "
    "When Bryan mentions 'my mailbox' or 'my mail', he means his email inbox. "
    "Answer only what he asked, in a few short sentences. No emojis, no filler, no inventing "
    "details. If you do not know something, say so in one line."
)


def env_items(path):
    items = {}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    items[key.strip()] = value.strip()
    return items


def write_env(path, items):
    with open(path, "w", encoding="utf-8") as f:
        for key, value in items.items():
            f.write(f"{key}={value}\n")


def load_env_file(path):
    global OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_CONTEXT_LENGTH, OLLAMA_VISION_MODEL
    for key, value in env_items(path).items():
        if key == "OLLAMA_HOST":
            OLLAMA_HOST = value
        elif key == "OLLAMA_MODEL":
            OLLAMA_MODEL = value
        elif key == "OLLAMA_VISION_MODEL":
            OLLAMA_VISION_MODEL = value
        elif key == "OLLAMA_CONTEXT_LENGTH":
            OLLAMA_CONTEXT_LENGTH = int(value) if value.isdigit() else 0


def _request(path, data=None):
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(OLLAMA_HOST + path, data=body, headers=headers)
    timeout = None if os.environ.get("OLLAMA_NO_TIMEOUT") else 600
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code} from {OLLAMA_HOST + path}: {detail}") from e


def models():
    tags = _request("/api/tags")
    return [m["name"] for m in tags.get("models", [])]


def chat_raw(model, messages, tools=None, fmt=None):
    payload = {"model": model, "messages": messages, "stream": False}
    if OLLAMA_CONTEXT_LENGTH:
        payload["options"] = {"num_ctx": OLLAMA_CONTEXT_LENGTH}
    if tools:
        payload["tools"] = tools
    if fmt:
        payload["format"] = fmt
    out = _request("/api/chat", payload)
    return out["message"]


def chat(model, message, system=None, fmt=None, images=None):
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    user = {"role": "user", "content": message}
    if images:
        user["images"] = images
    messages.append(user)
    return chat_raw(model, messages, fmt=fmt).get("content", "")


API = "https://api.telegram.org"


def api(token, method, data=None):
    url = f"{API}/bot{token}/{method}"
    body = json.dumps(data).encode() if data is not None else None
    headers = {"Content-Type": "application/json"} if data is not None else {}
    req = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=90) as resp:
        return json.loads(resp.read().decode())


def send(token, chat_id, text):
    return api(token, "sendMessage", {"chat_id": chat_id, "text": text})


def get_file_path(token, file_id):
    return api(token, "getFile", {"file_id": file_id})["result"]["file_path"]


def download_file(token, file_path):
    url = f"{API}/file/bot{token}/{file_path}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read()
