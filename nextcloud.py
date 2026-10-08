import base64
import os
import urllib.error
import urllib.parse
import urllib.request

import llm

ENV_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")


def _config():
    env = llm.env_items(ENV_FILE)
    url = env["NEXTCLOUD_URL"].rstrip("/")
    user = env["NEXTCLOUD_USER"]
    return url, user, env["NEXTCLOUD_APP_PASSWORD"]


def _dav(path, method="GET", data=None, headers=None):
    url, user, password = _config()
    safe = "/".join(urllib.parse.quote(seg) for seg in path.strip("/").split("/"))
    full = f"{url}/remote.php/dav/files/{user}/{safe}"
    auth = base64.b64encode(f"{user}:{password}".encode()).decode()
    request_headers = {"Authorization": f"Basic {auth}"}
    if headers:
        request_headers.update(headers)
    req = urllib.request.Request(full, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def ensure_dir(path):
    current = ""
    for part in path.strip("/").split("/"):
        current = f"{current}/{part}" if current else part
        status, _ = _dav(current, method="MKCOL")
        if status not in (201, 405):
            raise RuntimeError(f"Could not create Nextcloud folder {current!r} (HTTP {status})")


def upload(path, data):
    parent = path.rsplit("/", 1)[0] if "/" in path.strip("/") else ""
    if parent:
        ensure_dir(parent)
    status, body = _dav(
        path, method="PUT", data=data,
        headers={"Content-Type": "application/octet-stream"})
    if status not in (200, 201, 204):
        raise RuntimeError(f"Nextcloud upload failed (HTTP {status}): {body[:200]!r}")
    return status
