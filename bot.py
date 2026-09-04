import os
import sys

import llm

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env")
POLL_TIMEOUT = 30
TOKEN = ""


def ensure_env():
    env = llm.env_items(ENV_FILE)
    changed = False
    required = ("TELEGRAM_TOKEN", "OLLAMA_HOST")
    for key, prompt, default in (
        ("TELEGRAM_TOKEN", "Telegram bot token (from @BotFather): ", ""),
        ("TELEGRAM_CHAT_ID", "Your chat id (Enter to skip, auto-saved on first message): ", ""),
        ("OLLAMA_HOST", "Ollama host (Enter for localhost): ", "http://localhost:11434"),
        ("OLLAMA_MODEL", "Model name to use (Enter to auto-pick first installed): ", ""),
        ("OLLAMA_API_KEY", "", ""),
        ("SYSTEM_PROMPT", "", ""),
    ):
        if key not in env:
            if prompt:
                value = input(prompt).strip()
            else:
                value = default
            env[key] = value or default
            changed = True
        elif not env[key] and key in required:
            value = input(prompt).strip()
            env[key] = value or default
            changed = True
    if changed:
        llm.write_env(ENV_FILE, env)
        print(f"Saved to {ENV_FILE}")
    return env


def get_updates(offset):
    return llm.api(TOKEN, "getUpdates", {
        "timeout": POLL_TIMEOUT,
        "offset": offset,
        "allowed_updates": ["message"],
    })["result"]


def main():
    global TOKEN
    env = ensure_env()
    TOKEN = env["TELEGRAM_TOKEN"]
    llm.load_env_file(ENV_FILE)
    system_prompt = llm.SYSTEM_PROMPT or llm.DEFAULT_SYSTEM_PROMPT

    model = llm.OLLAMA_MODEL
    available = []
    try:
        available = llm.models()
    except Exception as e:
        print(f"Warning: cannot reach ollama at {llm.OLLAMA_HOST}: {e}")
    if not model:
        model = available[0] if available else ""
    if model not in available and available:
        model = available[0]
    if not model:
        print("No model available. Start ollama and pull a model first.")
        sys.exit(1)

    print(f"Bot running. Model: {model}")
    print(f"Chat id set: {env.get('TELEGRAM_CHAT_ID') or '(not yet - will auto-save on first message)'}")
    print("Ctrl+C to stop.")

    offset = 0
    while True:
        for update in get_updates(offset):
            offset = update["update_id"] + 1
            message = update.get("message")
            if not message or "text" not in message:
                continue
            chat_id = message["chat"]["id"]
            text = message["text"].strip()
            print(f"< {message['chat'].get('username', chat_id)}: {text}")

            current = llm.env_items(ENV_FILE).get("TELEGRAM_CHAT_ID", "")
            if not current:
                env = llm.env_items(ENV_FILE)
                env["TELEGRAM_CHAT_ID"] = str(chat_id)
                llm.write_env(ENV_FILE, env)
                print(f"Saved chat id {chat_id} to {ENV_FILE}")

            llm.send(TOKEN, chat_id, "Thanks for your message! We will get back to you as soon as possible")
            if text == "/start":
                llm.send(TOKEN, chat_id, f"Connected to local model: {model}. Send a message.")
                continue
            if text == "/chatid":
                llm.send(TOKEN, chat_id, str(chat_id))
                continue
            if text.startswith("/model"):
                name = text.split(None, 1)[1].strip()
                if name in available:
                    model = name
                    llm.send(TOKEN, chat_id, f"Switched to {model}")
                else:
                    llm.send(TOKEN, chat_id, f"Not installed: {name}. Available: {', '.join(available) or 'none'}")
                continue
            try:
                reply = llm.chat(model, text, system=system_prompt)
                print(f"> {reply}")
                llm.send(TOKEN, chat_id, reply)
            except Exception as e:
                print(f"Error: {e}")
                llm.send(TOKEN, chat_id, f"Error: {e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        sys.exit(0)
