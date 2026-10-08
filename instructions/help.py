from instructions import command, help_text


@command("help", "show this list")
def handle(ctx, args):
    return help_text()
