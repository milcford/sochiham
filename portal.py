#!/usr/bin/env python3
import json, os, subprocess
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode
from urllib.request import urlopen
import xml.etree.ElementTree as ET
HOST = os.environ.get("PORTAL_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORTAL_PORT", "8080"))
DB = os.environ.get("PGDATABASE", "sochiham")
DB_USER = os.environ.get("PGUSER", "sochiham")
DB_PASSWORD = os.environ.get("PGPASSWORD", "")
QRZ_USER = os.environ.get("QRZ_USER", "")
QRZ_PASSWORD = os.environ.get("QRZ_PASSWORD", "")
QRZ_SESSION = ""

def psql(sql):
    env = os.environ.copy()
    if DB_PASSWORD: env["PGPASSWORD"] = DB_PASSWORD
    proc = subprocess.run(["psql", "-h", "127.0.0.1", "-U", DB_USER, "-d", DB, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql], capture_output=True, text=True, env=env)
    if proc.returncode != 0: raise RuntimeError(proc.stderr.strip() or "ошибка базы")
    return proc.stdout
def q(value):
    return "'" + str(value).replace("'", "''") + "'"
def tag_text(root, name):
    for el in root.iter():
        if el.tag.split("}")[-1] == name and el.text: return el.text.strip()
    return ""
def qrz_login():
    global QRZ_SESSION
    login = ET.fromstring(urlopen("https://api.qrz.ru/login?" + urlencode({"u": QRZ_USER, "p": QRZ_PASSWORD, "agent": "sochiham"}), timeout=20).read())
    QRZ_SESSION = tag_text(login, "session_id")
    if not QRZ_SESSION: raise RuntimeError(tag_text(login, "error") or "qrz.ru не пустил")
def qrz_lookup(call):
    global QRZ_SESSION
    if not QRZ_USER or not QRZ_PASSWORD: raise RuntimeError("нет доступа к qrz")
    if not QRZ_SESSION: qrz_login()
    data = ET.fromstring(urlopen("https://api.qrz.ru/callsign?" + urlencode({"id": QRZ_SESSION, "callsign": call}), timeout=20).read())
    if tag_text(data, "error"):
        QRZ_SESSION = ""
        qrz_login()
        data = ET.fromstring(urlopen("https://api.qrz.ru/callsign?" + urlencode({"id": QRZ_SESSION, "callsign": call}), timeout=20).read())
        if tag_text(data, "error"):
            QRZ_SESSION = ""
            raise RuntimeError(tag_text(data, "error"))
    grid = tag_text(data, "locator") or tag_text(data, "grid")
    phone = tag_text(data, "phone") or tag_text(data, "tel") or tag_text(data, "telephone")
    return {"callsign": tag_text(data, "call") or call, "surname": tag_text(data, "surname"), "name": tag_text(data, "name"), "patronymic": tag_text(data, "name2"), "city": tag_text(data, "city").rstrip(","), "locator": grid[:4].upper(), "phone": phone}

class Handler(SimpleHTTPRequestHandler):
    def send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path.startswith("/api/locators"):
            rows = psql("SELECT code, title FROM locators ORDER BY code;")
            return self.send_json([dict(zip(["code","title"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        if self.path.startswith("/api/qrz"):
            call = parse_qs(self.path.split("?", 1)[-1]).get("call", [""])[0].strip().upper()
            try: return self.send_json(qrz_lookup(call))
            except Exception as exc: return self.send_json({"error": str(exc)}, 404)
        return super().do_GET()
    def do_POST(self):
        if not self.path.startswith("/api/join"): return self.send_error(404)
        length = int(self.headers.get("Content-Length", "0"))
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
        callsign = form.get("callsign", "").strip().upper()
        name = form.get("name", "").strip()
        if not callsign or not name: return self.send_json({"error": "нужны позывной и имя"}, 400)
        fields = dict(callsign=callsign, surname=form.get("surname",""), name=name, patronymic=form.get("patronymic",""), city=form.get("city",""), locator=form.get("locator","").upper(), phone=form.get("phone",""))
        found = psql(f"SELECT id FROM operators WHERE callsign={q(callsign)} LIMIT 1;").strip()
        if found:
            psql(f"UPDATE operators SET surname={q(fields['surname'])}, name={q(fields['name'])}, patronymic={q(fields['patronymic'])}, city={q(fields['city'])}, locator={q(fields['locator'])}, phone={q(fields['phone'])} WHERE id={int(found)};")
        else:
            psql(f"INSERT INTO operators (callsign, surname, name, patronymic, city, locator, phone) VALUES ({q(fields['callsign'])}, {q(fields['surname'])}, {q(fields['name'])}, {q(fields['patronymic'])}, {q(fields['city'])}, {q(fields['locator'])}, {q(fields['phone'])});")
        self.send_json({"ok": True})
    def log_message(self, fmt, *args): return
if __name__ == "__main__":
    print(f"Портал: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
