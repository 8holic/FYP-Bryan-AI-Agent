import importlib
import pkgutil

REGISTRY = {}


def command(name, description=""):
    def deco(fn):
        REGISTRY[name] = {"fn": fn, "description": description}
        return fn

    return deco


def help_text():
    lines = ["Commands:"]
    for name in sorted(REGISTRY):
        desc = REGISTRY[name]["description"]
        lines.append(f"/{name} - {desc}" if desc else f"/{name}")
    lines.append("")
    lines.append("Anything else you type is handled as a normal message by the assistant.")
    return "\n".join(lines)


def dispatch(ctx, text):
    parts = text[1:].split(None, 1)
    name = parts[0]
    args = parts[1] if len(parts) > 1 else ""
    entry = REGISTRY.get(name)
    if not entry:
        return f"Unknown command: /{name}. Type /help."
    return entry["fn"](ctx, args)


def load_all():
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")
