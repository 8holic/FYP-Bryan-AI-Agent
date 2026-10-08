import importlib
import pkgutil

REGISTRY = {}


def tool(description, parameters):
    def deco(fn):
        REGISTRY[fn.__name__] = {
            "fn": fn,
            "description": description,
            "parameters": parameters,
        }
        return fn

    return deco


def schemas():
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": entry["description"],
                "parameters": entry["parameters"],
            },
        }
        for name, entry in REGISTRY.items()
    ]


def call(name, arguments):
    entry = REGISTRY.get(name)
    if not entry:
        return f"Unknown tool: {name}"
    return entry["fn"](**arguments)


def load_all():
    for info in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{info.name}")
