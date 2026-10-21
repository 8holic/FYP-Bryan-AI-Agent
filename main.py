import sys
import threading

import bot
import mail

if __name__ == "__main__":
    try:
        bot.ensure_env()
        mail.ensure_email_env()
        threading.Thread(target=mail.run_poller, daemon=True, name="mail").start()
        bot.main()
    except KeyboardInterrupt:
        print()
        sys.exit(0)
