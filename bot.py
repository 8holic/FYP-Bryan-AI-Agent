import json
import os
import sys
import time
import urllib.error

import agent
import instructions
import llm

HERE = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(HERE, ".env")
POLL_TIMEOUT = 30
TOKEN = ""


class Ctx:
    def __init__(self, token, chat_id, env, model, system_prompt):
        self.token = token
        self.chat_id = chat_id
        self.env = env
        self.model = model
        self.system_prompt = system_prompt

    def send(self, text):
        llm.send(self.token, self.chat_id, text)


def ensure_env():
    env = llm.env_items(ENV_FILE)
    changed = False
    required = ("TELEGRAM_TOKEN", "OLLAMA_HOST")
    for key, prompt, default in (
        ("TELEGRAM_TOKEN", "Telegram bot token (from @BotFather): ", ""),
        ("TELEGRAM_CHAT_ID", "Your chat id (Enter to skip, auto-saved on first message): ", ""),
        ("OLLAMA_HOST", "Ollama host (Enter for localhost): ", "http://localhost:11434"),
        ("OLLAMA_MODEL", "Model name to use (Enter to auto-pick first installed): ", ""),
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


def poll_updates(offset):
    delay = 5
    while True:
        try:
            return get_updates(offset)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5
                try:
                    wait = json.loads(e.read().decode())["parameters"]["retry_after"]
                except Exception:
                    pass
                print(f"Telegram rate limit; waiting {wait}s")
                time.sleep(wait)
            elif e.code == 401:
                print("Telegram rejected the token (401). Check TELEGRAM_TOKEN in .env.")
                time.sleep(60)
            elif e.code == 409:
                print("Telegram conflict (409): another bot instance is polling. Stop the other one.")
                time.sleep(60)
            else:
                print(f"Telegram error {e.code}; retrying in {delay}s")
                time.sleep(delay)
                delay = min(delay * 2, 60)
        except Exception as e:
            print(f"Telegram poll error: {e}; retrying in {delay}s")
            time.sleep(delay)
            delay = min(delay * 2, 60)


def main():
    global TOKEN
    env = ensure_env()
    TOKEN = env["TELEGRAM_TOKEN"]
    llm.load_env_file(ENV_FILE)
    instructions.load_all()
    system_prompt = llm.DEFAULT_SYSTEM_PROMPT

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
        for update in poll_updates(offset):
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

            ctx = Ctx(TOKEN, chat_id, llm.env_items(ENV_FILE), model, system_prompt)

            if text.startswith("/"):
                try:
                    reply = instructions.dispatch(ctx, text)
                except Exception as e:
                    reply = f"Command error: {e}"
                if reply:
                    print(f"> {reply}")
                    llm.send(TOKEN, chat_id, reply)
                continue

            try:
                reply = agent.run(model, text, system_prompt)
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
