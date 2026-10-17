# FYP Bryan AI Agent

A local AI assistant that watches a Gmail inbox and reports to Telegram, powered by a local Ollama model. No cloud LLM — everything runs on your own machine.

## What it does

- Polls Gmail for new unread mail (last 24h, promotions excluded).
- Classifies each email with the local model: **urgent** mail is summarized and pushed to Telegram immediately; **non-urgent** mail is queued as a compact note (sender, subject, snippet).
- Flushes the queue on demand (Telegram `/mailbox`) or automatically at **08:00** local time: **one** model call turns the whole batch into a single digest, then the queue is cleared.
- OCRs inline images in emails (RapidOCR) and feeds the extracted text to the model, so image-only mail still gets summarized.
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
- **Gmail** address plus an **App Password** (Google Account → Security → 2-Step Verification → App passwords). IMAP is always on for personal Gmail, so no OAuth client or consent flow is needed.

## Configuration

Copy `.env` to the repo root (it is gitignored) and fill it in:

```
TELEGRAM_TOKEN=
TELEGRAM_CHAT_ID=
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=qwen3:8b
OLLAMA_CONTEXT_LENGTH=16384
GMAIL_ADDRESS=
GMAIL_APP_PASSWORD=
```

| Key | Notes |
|---|---|
| `TELEGRAM_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | auto-saved on first message if left blank |
| `OLLAMA_HOST` | `http://localhost:11434`, or a LAN IP if Ollama runs elsewhere |
| `OLLAMA_MODEL` | blank auto-picks the first installed model |
| `OLLAMA_CONTEXT_LENGTH` | optional; omit to use the model default |
| `GMAIL_ADDRESS` | the Gmail account to read |
| `GMAIL_APP_PASSWORD` | 16-character app password, no spaces; requires 2-Step Verification |

An app password is revoked when the Google Account password changes; generate a new one and update `.env` after any password change.

## Run

```
.venv/bin/python main.py
```

On Windows: `.\.venv\Scripts\python.exe main.py`.

## Telegram commands

| Command | Action |
|---|---|
| `/start` | show the connected model |
| `/chatid` | print your chat id |
| `/model <name>` | switch model |
| `/mailbox` | send a digest of the collected non-urgent mail now (any message containing "mailbox" works) |

## Notes

- Runtime state files (`.gmail_seen.json`, `.mailbox_summary.md`) are gitignored and created automatically.
- The 08:00 flush uses the server's local time, and the Gmail IMAP query asks for unread mail from the last 24 hours — set the clock with `timedatectl`.
- The summary is sent as a single Telegram message (4096-char cap); very busy days could exceed it.
