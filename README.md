# FYP Bryan AI Agent

A local AI assistant that watches an IMAP inbox (Gmail by default) and reports to Telegram, powered by a local Ollama model. No cloud LLM — everything runs on your own machine.

## What it does

- Polls any IMAP inbox for **all unread** mail, newest first, and excludes already-processed mail by marking it read on the server.
- Classifies each email with the local model: **urgent** mail is summarized and pushed to Telegram immediately; **non-urgent** mail is queued as a compact note (sender, subject, snippet). Past deadlines are not urgent.
- Flushes the queue on demand (Telegram `/mailbox`) or automatically at **08:00** local time: **one** model call turns the whole batch into a single digest, then the queue is cleared.
- OCRs inline images in emails (RapidOCR) and feeds the extracted text to the model, so image-only mail still gets summarized.
- Marks processed mail as read and tags it `AI Read` (Gmail label; `AIRead` keyword elsewhere).
- Doubles as a Telegram chat bot backed by the same local model.

## Requirements

### System packages (Ubuntu)

```
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip curl libgl1 libglib2.0-0
```

| Package | Why |
|---|---|
| `git` | clone the repo |
| `python3`, `python3-venv`, `python3-pip` | run the app in a venv |
| `curl` | fetch the Ollama installer |
| `libgl1`, `libglib2.0-0` | OpenCV runtime libs — **required** or OCR fails to import on a headless box |

Python **3.10+**.

### Ollama

```
curl -fsSL https://ollama.com/install.sh | sh
ollama pull qwen3:8b
```

CPU-only is fine (the i5 server has no usable GPU). An 8B model takes roughly 30s per email on CPU, which is acceptable for a 24/7 bot.

### Python packages

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` pins `rapidocr-onnxruntime==1.2.3` (the version installable on both Python 3.12 and 3.14). It pulls in `onnxruntime`, `opencv-python`, `numpy`, `Pillow`, `pyclipper`, `shapely`, `PyYAML`, `protobuf`, `flatbuffers`, `six`, and `packaging`. OCR models ship inside the wheel — nothing extra to download.

### External services

- **Telegram** bot token from [@BotFather](https://t.me/BotFather).
- **IMAP** account. Gmail needs an **App Password** (Google Account → Security → 2-Step Verification → App passwords); other providers need IMAP enabled and an app password where required. OAuth-only providers are not supported.

## Configuration

Copy `.env` to the repo root (it is gitignored) and fill it in:

```
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_CONTEXT_LENGTH=16384
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
IMAP_ADDRESS=
IMAP_PASSWORD=
```

| Key | Notes |
|---|---|
| `TELEGRAM_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | the only chat id the bot accepts; also receives notifications |
| `OLLAMA_HOST` | `http://localhost:11434`, or a LAN IP if Ollama runs elsewhere |
| `OLLAMA_MODEL` | blank auto-picks the first installed model |
| `OLLAMA_CONTEXT_LENGTH` | optional; omit to use the model default |
| `IMAP_HOST` | IMAP server; defaults to `imap.gmail.com` |
| `IMAP_PORT` | IMAP SSL port; defaults to `993` |
| `IMAP_ADDRESS` | address to log in with |
| `IMAP_PASSWORD` | password or app password, no spaces |

`GMAIL_ADDRESS` and `GMAIL_APP_PASSWORD` are still accepted as fallbacks. An app password is revoked when the Google Account password changes; generate a new one and update `.env` after any password change.

## Run

```
.venv/bin/python main.py
```

On Windows: `.\.venv\Scripts\python.exe main.py`.

## Telegram commands

| Command | Action |
|---|---|
| `/start` | show the connected model |
| `/mailbox` | send a digest of the collected non-urgent mail now (any message containing "mailbox" works) |

## Notes

- Runtime state (`.mailbox_summary.md`, `mail.db`) is gitignored and created automatically.
- The 08:00 flush uses the server's local time.
- Polls every 5 minutes; set `EMAIL_POLL_SECONDS` to change it.
- Processed mail is marked read, so it is not polled again. Use `IMAP_HOST`/`IMAP_PORT` to point at a non-Gmail server.
- The summary is sent as a single Telegram message (4096-char cap); very busy days could exceed it.
