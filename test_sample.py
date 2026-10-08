import base64
import email
import glob
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import llm
import mail

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE_DIR = os.path.join(HERE, "samplemail")
MAX_IMAGES = int(sys.argv[1]) if len(sys.argv) > 1 else 4


def load_eml(path):
    with open(path, "rb") as f:
        msg = email.message_from_bytes(f.read())
    subject = msg["subject"] or ""
    sender = msg["from"] or ""
    body = ""
    images = []
    for part in msg.walk():
        ctype = part.get_content_type()
        if ctype == "text/plain" and not body:
            body = part.get_payload(decode=True).decode("utf-8", "replace")
        elif ctype.startswith("image/"):
            data = part.get_payload(decode=True) or b""
            if data:
                images.append(base64.b64encode(data).decode())
    return sender, subject, body, images


def ocr_images(b64_list):
    from rapidocr_onnxruntime import RapidOCR

    ocr = RapidOCR()
    lines = []
    for b in b64_list:
        result, _ = ocr(base64.b64decode(b))
        if result:
            lines.extend(line[1] for line in result)
    return "\n".join(lines)


def report(title, model, sender, subject, body):
    print(f"\n----- {title} -----")
    body = body[:4000]
    t = time.time()
    try:
        urgent = mail.classify(model, sender, subject, body)
        summary = mail.summarize(model, sender, subject, body)
    except Exception as e:
        print(f"FAILED: {e}")
        return
    print(f"urgent: {urgent}  ->  {'NOTIFY immediately' if urgent else 'queue for digest'}")
    print(f"summary ({time.time() - t:.1f}s):\n{summary}")


def main():
    llm.load_env_file(mail.ENV_FILE)
    paths = sorted(glob.glob(os.path.join(SAMPLE_DIR, "*.eml")))
    if not paths:
        sys.exit(f"No .eml files in {SAMPLE_DIR}")

    try:
        installed = llm.models()
    except Exception as e:
        sys.exit(f"Cannot reach ollama at {llm.OLLAMA_HOST}: {e}")
    print(f"ollama models: {', '.join(installed) or 'none'}")

    forced = os.environ.get("TEST_MODEL")
    model = forced or llm.OLLAMA_MODEL or (installed[0] if installed else "")
    if not forced and model not in installed and installed:
        model = installed[0]
    vision = forced or llm.OLLAMA_VISION_MODEL or model
    if not model:
        sys.exit("No model available.")
    print(f"text model:   {model}")
    print(f"vision model: {vision}")
    if vision not in installed:
        print(f"WARNING: vision model {vision} is not installed; pull it first.")

    for path in paths:
        sender, subject, body, images = load_eml(path)
        print("\n" + "=" * 70)
        print(f"file:  {os.path.basename(path)}")
        print(f"from:  {sender}")
        print(f"subj:  {subject}")
        print(f"body:  {len(body)} chars | images: {len(images)}")
        print("preview:", " ".join(body.split())[:300])

        if len(images) > MAX_IMAGES:
            print(f"(using first {MAX_IMAGES} of {len(images)} images; pass a number to change)")
            used = images[:MAX_IMAGES]
        else:
            used = images

        ocr_text = ""
        if used:
            try:
                ocr_text = ocr_images(used)
            except Exception as e:
                print(f"OCR failed: {e}")
        body_ocr = body + ("\n[Image text]\n" + ocr_text if ocr_text else "")
        report("TODAY: text + OCR", model, sender, subject, body_ocr)

        if used:
            vision_text = ""
            print(f"\n----- VISION: {vision} on {len(used)} image(s) -----")
            t = time.time()
            try:
                vision_text = mail.vision_read(vision, sender, subject, used)
            except Exception as e:
                print(f"FAILED: {e}")
            if vision_text:
                print(f"({time.time() - t:.1f}s)\n{vision_text}")
                body_vis = body + "\n[Image content]\n" + vision_text
                report("VISION: text + vision", model, sender, subject, body_vis)


if __name__ == "__main__":
    main()
