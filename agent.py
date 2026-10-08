import json

import llm
import tools


def run(model, text, system, max_steps=8):
    tools.load_all()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": text})

    for _ in range(max_steps):
        message = llm.chat_raw(model, messages, tools=tools.schemas_for(text) or None)
        messages.append(message)
        calls = message.get("tool_calls")
        if not calls:
            return message.get("content", "")
        for call in calls:
            name = call["function"]["name"]
            args = call["function"].get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    args = {}
            try:
                result = tools.call(name, args)
            except Exception as e:
                result = f"Tool error: {e}"
            messages.append({"role": "tool", "content": str(result)})

    return "Stopped: too many tool calls."
