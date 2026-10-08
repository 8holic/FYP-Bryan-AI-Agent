from instructions import command


@command("start", "connect and show the active model")
def handle(ctx, args):
    return f"Connected to local model: {ctx.model}. Send a message."
