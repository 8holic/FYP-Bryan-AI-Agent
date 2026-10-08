import importlib
import inspect
import pkgutil

REGISTRY = {}


def tool(description, parameters, triggers=None):
    def deco(fn):
        REGISTRY[fn.__name__] = {
            "fn": fn,
            "description": description,
            "parameters": parameters,
            "triggers": [t.lower() for t in (triggers or [])],
        }
        return fn

    return deco


def _schema(name, entry):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": entry["description"],
            "parameters": entry["parameters"],
        },
    }


def schemas():
    return [_schema(name, entry) for name, entry in REGISTRY.items()]


def schemas_for(text):
    lowered = text.lower()
    return [
        _schema(name, entry)
        for name, entry in REGISTRY.items()
        if not entry["triggers"] or any(t in lowered for t in entry["triggers"])
    ]


def call(name, arguments, ctx=None):
    entry = REGISTRY.get(name)
    if not entry:
        return f"Unknown tool: {name}"
    fn = entry["fn"]
    if ctx is not None and "ctx" in inspect.signature(fn).parameters:
        arguments = {**arguments, "ctx": ctx}
    return fn(**arguments)


def load_all():
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")
