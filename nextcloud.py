import base64
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import llm

ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
WALK_TTL = 300
_walk_cache = {"t": 0.0, "files": []}


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


def _propfind(path, depth=1):
    body = (
        b'<?xml version="1.0"?>'
        b'<d:propfind xmlns:d="DAV:"><d:prop>'
        b"<d:resourcetype/><d:getcontentlength/><d:getlastmodified/>"
        b"</d:prop></d:propfind>"
    )
    status, data = _dav(
        path, method="PROPFIND", data=body,
        headers={"Depth": str(depth), "Content-Type": "application/xml"})
    if status != 207:
        return []
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return []
    ns = {"d": "DAV:"}
    entries = []
    for resp in root.findall("d:response", ns):
        prop = resp.find("d:propstat/d:prop", ns)
        if prop is None:
            continue
        entries.append({
            "href": resp.findtext("d:href", default="", namespaces=ns),
            "dir": prop.find("d:resourcetype/d:collection", ns) is not None,
            "size": int(prop.findtext("d:getcontentlength", default="0", namespaces=ns) or 0),
            "mtime": prop.findtext("d:getlastmodified", default="", namespaces=ns),
        })
    return entries


def _relative(href, user):
    href = urllib.parse.unquote(href)
    marker = f"/files/{user}/"
    idx = href.find(marker)
    return href[idx + len(marker):].strip("/") if idx != -1 else ""


def walk_files(root="", limit=5000):
    global _walk_cache
    now = time.time()
    if not root and _walk_cache["files"] and now - _walk_cache["t"] < WALK_TTL:
        return _walk_cache["files"]
    _, user, _ = _config()
    files = []
    stack = [root]
    seen = set()
    while stack and len(files) < limit:
        folder = stack.pop()
        for entry in _propfind(folder, 1):
            rel = _relative(entry["href"], user)
            if rel in seen:
                continue
            seen.add(rel)
            if rel == folder:
                continue
            if entry["dir"]:
                stack.append(rel)
            elif rel:
                files.append({"path": rel, "size": entry["size"], "mtime": entry["mtime"]})
    if not root:
        _walk_cache = {"t": now, "files": files}
    return files


def download(path):
    status, data = _dav(path, method="GET")
    if status != 200:
        raise RuntimeError(f"Nextcloud download failed for {path!r} (HTTP {status})")
    return data
