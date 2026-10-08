import mail
from instructions import command


@command("mailbox", "send a digest of collected mail now")
def handle(ctx, args):
    try:
        sent = mail.flush_summary(ctx.env, ctx.model)
    except Exception as e:
        return f"Mailbox error: {e}"
    if not sent:
        return "No new mail to summarize."
    return None
