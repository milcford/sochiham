#!/usr/bin/env python3
import hashlib, hmac, json, os, secrets, subprocess, xml.etree.ElementTree as ET
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode
from urllib.request import urlopen
HOST = os.environ.get("ADMIN_HOST", "127.0.0.1")
PORT = int(os.environ.get("ADMIN_PORT", "8081"))
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
DB = os.environ.get("PGDATABASE", "sochiham")
DB_USER = os.environ.get("PGUSER", "sochiham")
DB_PASSWORD = os.environ.get("PGPASSWORD", "")
QRZ_USER = os.environ.get("QRZ_USER", "")
QRZ_PASSWORD = os.environ.get("QRZ_PASSWORD", "")
SECRET = os.environ.get("ADMIN_SECRET", secrets.token_hex(16))
QRZ_SESSION = ""

def token():
    return hmac.new(SECRET.encode(), b"admin-ok", hashlib.sha256).hexdigest()
def same_password(given):
    return hmac.compare_digest(given.encode(), PASSWORD.encode())
def psql(sql):
    env = os.environ.copy()
    if DB_PASSWORD: env["PGPASSWORD"] = DB_PASSWORD
    proc = subprocess.run(["psql", "-h", "127.0.0.1", "-U", DB_USER, "-d", DB, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql], capture_output=True, text=True, env=env)
    if proc.returncode != 0: raise RuntimeError(proc.stderr.strip() or "ошибка базы")
    return proc.stdout
def q(value):
    return "'" + str(value).replace("'", "''") + "'"
def open_json(handler, payload, code=200):
    body = json.dumps(payload, ensure_ascii=False).encode()
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers(); handler.wfile.write(body)
def qrz_xml(url):
    with urlopen(url, timeout=20) as resp: return ET.fromstring(resp.read())
def tag_text(root, name):
    for el in root.iter():
        if el.tag.endswith(name) and el.text: return el.text.strip()
    return ""
def qrz_lookup(call):
    global QRZ_SESSION
    if not QRZ_USER or not QRZ_PASSWORD: raise RuntimeError("не заданы QRZ_USER и QRZ_PASSWORD")
    if not QRZ_SESSION:
        login = qrz_xml("https://api.qrz.ru/login?" + urlencode({"u": QRZ_USER, "p": QRZ_PASSWORD, "agent": "sochiham"}))
        QRZ_SESSION = tag_text(login, "session_id")
        if not QRZ_SESSION: raise RuntimeError(tag_text(login, "error") or "qrz.ru не пустил")
    data = qrz_xml("https://api.qrz.ru/callsign?" + urlencode({"id": QRZ_SESSION, "callsign": call}))
    if tag_text(data, "error"):
        QRZ_SESSION = ""
        raise RuntimeError(tag_text(data, "error"))
    return {"callsign": tag_text(data, "call") or call, "surname": tag_text(data, "surname"), "name": tag_text(data, "name"), "patronymic": tag_text(data, "name2"), "city": tag_text(data, "city").rstrip(","), "locator": (tag_text(data, "locator") or tag_text(data, "grid"))[:4]}

PAGE = open(os.path.join(os.path.dirname(__file__), "admin-page.html"), encoding="utf-8").read() if os.path.exists(os.path.join(os.path.dirname(__file__), "admin-page.html")) else ""
