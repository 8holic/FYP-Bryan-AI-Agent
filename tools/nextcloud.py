import base64
import os
import re

import llm
import nextcloud
from tools import tool

TRIGGERS = ["nextcloud", "cloud", "send me", "send the", "photo", "picture",
            "image", "file", "document", "download"]

STOPWORDS = {
    "send", "me", "the", "a", "an", "my", "from", "of", "to", "please", "can",
    "you", "give", "find", "get", "show", "file", "photo", "picture", "image",
    "document", "nextcloud", "cloud", "and", "with", "that", "is", "it", "want",
    "i", "in", "for", "on", "at", "have",
}

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
PHOTO_MAX = 10 * 1024 * 1024
MAX_CANDIDATES = 3

DESCRIPTION = "Find a file in the user's Nextcloud matching a description and send it to them."
PARAMETERS = {
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "What the user wants, e.g. 'an outdoor photo from Spain'.",
        }
    },
    "required": ["prompt"],
}


def _tokens(text):
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 1]


def _candidates(prompt):
    tokens = _tokens(prompt)
    if not tokens:
        return []
    scored = []
    for entry in nextcloud.walk_files(""):
        low = entry["path"].lower()
        score = sum(1 for t in tokens if t in low)
        if score:
            scored.append((score, entry["path"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [path for _, path in scored]


def _fits(ctx, path, prompt):
    data = nextcloud.download(path)
    image = [base64.b64encode(data).decode()]
    question = (
        f"Request: {prompt}\n"
        "Does this image match the request? Reply with YES or NO followed by a very short reason."
    )
    answer = llm.chat(ctx.model, question, images=image).strip()
    return answer.lower().startswith("yes"), answer.replace("\n", " ")[:200]


def _send(ctx, path):
    data = nextcloud.download(path)
    name = os.path.basename(path)
    if os.path.splitext(name)[1].lower() in IMAGE_EXTS and len(data) <= PHOTO_MAX:
        llm.send_photo(ctx.token, ctx.chat_id, name, data)
    else:
        llm.send_document(ctx.token, ctx.chat_id, name, data)
    return f"Sent {path}"


@tool(DESCRIPTION, PARAMETERS, triggers=TRIGGERS)
def send_file(prompt, ctx=None):
    candidates = _candidates(prompt)
    if not candidates:
        return "No file matched that description."
    if len(candidates) == 1:
        return _send(ctx, candidates[0])
    for path in candidates[:MAX_CANDIDATES]:
        try:
            ok, _ = _fits(ctx, path, prompt)
        except Exception:
            ok = False
        if ok:
            return _send(ctx, path)
    listing = ", ".join(candidates[:5])
    return f"No candidate clearly fit '{prompt}'. Closest matches: {listing}"
