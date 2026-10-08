import mail.store as store
from tools import tool

DESCRIPTION = "Search the user's stored emails by keyword."
PARAMETERS = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Words, a name, or a topic to search for in sender, subject or body.",
        },
        "limit": {
            "type": "integer",
            "description": "Maximum number of emails to return (default 5).",
        },
    },
    "required": ["query"],
}


@tool(DESCRIPTION, PARAMETERS, triggers=["email", "mail", "inbox"])
def search_email(query, limit=5):
    rows = store.search(query, limit)
    if not rows:
        return "No emails matched that search."
    return "\n\n".join(
        f"[{mid}] {subject} - {sender} ({date})\n{snippet}"
        for mid, sender, subject, date, snippet in rows
    )
