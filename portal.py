#!/usr/bin/env python3
import hashlib, hmac, json, os, secrets, smtplib, subprocess, xml.etree.ElementTree as ET
from email.message import EmailMessage
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode
from urllib.request import Request, urlopen
HOST = os.environ.get("PORTAL_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORTAL_PORT", "8080"))
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
def host_token():
    return hmac.new(SECRET.encode(), b"host-ok", hashlib.sha256).hexdigest()
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
def ensure_round():
    psql("CREATE TABLE IF NOT EXISTS round_host (id int PRIMARY KEY, callsign text DEFAULT '', password text DEFAULT '', current_call text DEFAULT '', current_name text DEFAULT '');")
    psql("INSERT INTO round_host (id) VALUES (1) ON CONFLICT DO NOTHING;")
    psql("CREATE TABLE IF NOT EXISTS round_queue (id serial PRIMARY KEY, pos int DEFAULT 0, callsign text, name text);")
def round_view():
    host = psql("SELECT COALESCE(callsign,''), COALESCE(current_call,''), COALESCE(current_name,'') FROM round_host WHERE id=1;").strip().split("|")
    rows = psql("SELECT id, callsign, COALESCE(name,'') FROM round_queue ORDER BY pos, id;")
    queue = [dict(zip(["id","callsign","name"], line.split("|"))) for line in rows.splitlines() if line.strip()]
    return {"host": host[0] if host and host[0] else "", "current_call": host[1] if len(host)>1 else "", "current_name": host[2] if len(host)>2 else "", "queue": queue}
def tag_text(root, name):
    for el in root.iter():
        if el.tag.split("}")[-1] == name and el.text: return el.text.strip()
    return ""


def ham_token(callsign):
    return hmac.new(SECRET.encode(), ("ham-" + callsign).encode(), hashlib.sha256).hexdigest()

def ensure_login():
    psql("""CREATE TABLE IF NOT EXISTS site_accounts (
      callsign text PRIMARY KEY,
      name text NOT NULL,
      email text NOT NULL,
      password text NOT NULL,
      reset_token text DEFAULT '',
      reset_until timestamptz
    );""")

def hash_password(password):
    salt = secrets.token_hex(8)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000).hex()
    return f"pbkdf2${salt}${digest}"

def password_ok(stored, given):
    if not stored:
        return False
    if stored.startswith("pbkdf2$"):
        _, salt, digest = stored.split("$", 2)
        check = hashlib.pbkdf2_hmac("sha256", given.encode(), salt.encode(), 100000).hex()
        return hmac.compare_digest(check, digest)
    return hmac.compare_digest(given.encode(), stored.encode())

def send_mail(to, subject, body):
    host = os.environ.get("SMTP_HOST", "")
    if not host:
        raise RuntimeError("Почта на сервере ещё не настроена.")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    sender = os.environ.get("SMTP_FROM", user)
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body, charset="utf-8")
    with smtplib.SMTP(host, port, timeout=20) as smtp:
        smtp.starttls()
        if user:
            smtp.login(user, password)
        smtp.sendmail(sender, [to], msg.as_bytes())

def ham_from_cookie(header):
    parts = {}
    for bit in (header or "").split(";"):
        if "=" in bit:
            k, v = bit.strip().split("=", 1)
            parts[k] = v
    call = parts.get("ham_call", "").upper()
    if call and parts.get("ham") == ham_token(call):
        return call
    return ""

def ham_profile(callsign):
    row = psql(f"SELECT callsign, COALESCE(name,''), COALESCE(password,''), COALESCE(email,'') FROM site_accounts WHERE callsign={q(callsign)} LIMIT 1;").strip()
    if not row:
        return None
    bits = row.split("|")
    return {"callsign": bits[0], "name": bits[1] if len(bits)>1 else "", "password": bits[2] if len(bits)>2 else "", "email": bits[3] if len(bits)>3 else ""}

def ensure_chat():
    psql("CREATE TABLE IF NOT EXISTS chat_messages (id bigserial PRIMARY KEY, room text NOT NULL, callsign text NOT NULL, name text NOT NULL, body text NOT NULL, created_at timestamptz NOT NULL DEFAULT now());")
    if psql("SELECT COUNT(*) FROM chat_messages;").strip() == "0":
        psql("INSERT INTO chat_messages (room, callsign, name, body) VALUES ('general','RZ6D','Сергей','Добро пожаловать в общий чат. Пишите позывной и коротко, как в эфире.'), ('general','R6A','Клуб','Воскресный круглый стол — в соседней комнате. Частота на главной.'), ('table','RZ6D','Сергей','Кто будет в воскресенье — отметьтесь здесь.'), ('market','RW6YYY','Пётр','Отдам кусок кабеля, Сочи, самовывоз.');")

def chat_messages(room, before=0, after=0):
    ensure_chat()
    where = f"room={q(room)}"
    order = "DESC"
    if after:
        where += f" AND id>{int(after)}"
        order = "ASC"
    elif before:
        where += f" AND id<{int(before)}"
    raw = psql("SELECT COALESCE(json_agg(row_to_json(t)), '[]') FROM (SELECT id, room, callsign, name, body AS text, to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS created_at FROM chat_messages WHERE " + where + " ORDER BY id " + order + " LIMIT 40) t;")
    rows = json.loads(raw.strip() or "[]")
    if order == "DESC":
        rows.reverse()
    return rows

def qrz_call(path, params):
    url = "https://api.qrz.ru/" + path + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "sochiham/1.0"})
    try:
        with urlopen(req, timeout=20) as resp: raw = resp.read()
    except HTTPError as exc:
        raw = exc.read()
    return ET.fromstring(raw)
def qrz_login():
    global QRZ_SESSION
    login = qrz_call("login", {"u": QRZ_USER, "p": QRZ_PASSWORD, "agent": "sochiham"})
    QRZ_SESSION = tag_text(login, "session_id")
    if not QRZ_SESSION: raise RuntimeError(tag_text(login, "error") or "qrz.ru не пустил")
def qrz_lookup(call):
    global QRZ_SESSION
    if not QRZ_USER or not QRZ_PASSWORD: raise RuntimeError("нет доступа к qrz")
    QRZ_SESSION = ""
    qrz_login()
    data = qrz_call("callsign", {"id": QRZ_SESSION, "callsign": call})
    if tag_text(data, "error"):
        raise RuntimeError(tag_text(data, "error"))
    grid = tag_text(data, "locator") or tag_text(data, "grid")
    phone = tag_text(data, "phone") or tag_text(data, "tel") or tag_text(data, "telephone")
    return {"callsign": tag_text(data, "call") or call, "surname": tag_text(data, "surname"), "name": tag_text(data, "name"), "patronymic": tag_text(data, "name2"), "city": tag_text(data, "city").rstrip(","), "locator": grid[:4].upper(), "phone": phone}

class Handler(SimpleHTTPRequestHandler):
    def cookie_ok(self):
        return f"admin={token()}" in self.headers.get("Cookie", "").split("; ")
    def host_ok(self):
        return f"host={host_token()}" in self.headers.get("Cookie", "").split("; ")
    def send_json(self, payload, code=200):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def send_html(self, body, code=200, extra=None):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if extra: self.send_header("Set-Cookie", extra)
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/admin", "/admin/"):
            return self.send_html(ADMIN_PAGE)
        if path == "/api/round":
            return self.send_json(round_view())
        if path == "/api/me":
            call = ham_from_cookie(self.headers.get("Cookie", ""))
            if not call:
                return self.send_json({"callsign": ""})
            person = ham_profile(call) or {"callsign": call, "name": ""}
            return self.send_json({"callsign": person["callsign"], "name": person["name"]})
        if path == "/api/messages":
            if not ham_from_cookie(self.headers.get("Cookie", "")):
                return self.send_json({"error": "Сначала войдите по позывному."}, 401)
            qs = parse_qs(self.path.split("?", 1)[-1])
            room = (qs.get("room") or ["general"])[0]
            if room not in ("general", "table", "market"):
                room = "general"
            try:
                before = int((qs.get("before") or ["0"])[0])
                after = int((qs.get("after") or ["0"])[0])
            except ValueError:
                before = after = 0
            try:
                return self.send_json({"messages": chat_messages(room, before, after)})
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        if path == "/api/logs":
            if not self.cookie_ok(): return self.send_json({"error": "auth"}, 401)
            rows = psql("SELECT id, my_call, dx_call, band, to_char(worked_at, 'DD.MM HH24:MI') FROM contest_logs ORDER BY worked_at DESC;")
            return self.send_json([dict(zip(["id","my_call","dx_call","band","worked_at"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        if path.startswith("/api/qso"):
            rows = psql("SELECT a.my_call, a.dx_call, a.band, to_char(a.worked_at, 'DD.MM HH24:MI'), COALESCE(round(2*6371*asin(sqrt(power(sin(radians(b.my_lat-a.my_lat)/2),2)+cos(radians(a.my_lat))*cos(radians(b.my_lat))*power(sin(radians(b.my_lon-a.my_lon)/2),2))))::text,''), CASE WHEN b.id IS NOT NULL THEN 'yes' ELSE 'no' END, COALESCE(a.my_locator,''), COALESCE(b.my_locator,'') FROM contest_logs a LEFT JOIN LATERAL (SELECT * FROM contest_logs b WHERE b.my_call=a.dx_call AND b.dx_call=a.my_call AND b.band=a.band AND b.my_lat IS NOT NULL AND abs(extract(epoch FROM (b.worked_at-a.worked_at)))<=900 ORDER BY abs(extract(epoch FROM (b.worked_at-a.worked_at))) LIMIT 1) b ON true ORDER BY a.worked_at DESC;")
            return self.send_json([dict(zip(["my_call","dx_call","band","worked_at","km","pair","my_loc","dx_loc"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        if path.startswith("/api/calls"):
            rows = psql("SELECT callsign, COALESCE(name,''), COALESCE(locator,'') FROM operators ORDER BY callsign;")
            return self.send_json([dict(zip(["callsign","name","locator"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        if path.startswith("/api/locators"):
            rows = psql("SELECT code, title FROM locators ORDER BY code;")
            return self.send_json([dict(zip(["code","title"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        if path.startswith("/api/qrz"):
            call = parse_qs(self.path.split("?", 1)[-1]).get("call", [""])[0].strip().upper()
            try: return self.send_json(qrz_lookup(call))
            except Exception as exc: return self.send_json({"error": str(exc)}, 404)
        if path.startswith("/api/operators"):
            if not self.cookie_ok(): return self.send_json({"error": "auth"}, 401)
            rows = psql("SELECT id, callsign, COALESCE(surname,''), name, COALESCE(patronymic,''), COALESCE(city,''), COALESCE(locator,''), COALESCE(phone,'') FROM operators ORDER BY callsign;")
            return self.send_json([dict(zip(["id","callsign","surname","name","patronymic","city","locator","phone"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        return super().do_GET()
    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
    def delete_log(self, row_id):
        if row_id == "all":
            psql("DELETE FROM contest_logs;")
        else:
            psql(f"DELETE FROM contest_logs WHERE id={int(row_id)};")
    def set_ham_cookie(self, callsign):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        body = b'{"ok":true}'
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Set-Cookie", f"ham={ham_token(callsign)}; HttpOnly; Path=/; Max-Age=2592000")
        self.send_header("Set-Cookie", f"ham_call={callsign}; Path=/; Max-Age=2592000")
        self.end_headers()
        self.wfile.write(body)
    def enter(self, form):
        ensure_login()
        callsign = form.get("callsign", "").strip().upper()
        password = form.get("password", "")
        person = ham_profile(callsign)
        if not person or not person["password"] or not password_ok(person["password"], password):
            return self.send_json({"error": "Неверный позывной или пароль."}, 403)
        return self.set_ham_cookie(callsign)
    def register(self, form):
        ensure_login()
        callsign = form.get("callsign", "").strip().upper()
        name = form.get("name", "").strip()[:40]
        email = form.get("email", "").strip().lower()[:80]
        password = form.get("password", "")
        if len(callsign) < 3 or not name or "@" not in email or len(password) < 4:
            return self.send_json({"error": "Нужны позывной, имя, почта и пароль от 4 знаков."}, 400)
        if ham_profile(callsign):
            return self.send_json({"error": "Такой позывной уже есть. Войдите или восстановите пароль."}, 409)
        taken = psql(f"SELECT callsign FROM site_accounts WHERE lower(email)={q(email)} LIMIT 1;").strip()
        if taken:
            return self.send_json({"error": "Эта почта уже занята."}, 409)
        psql(f"INSERT INTO site_accounts (callsign, name, email, password) VALUES ({q(callsign)}, {q(name)}, {q(email)}, {q(hash_password(password))});")
        found = psql(f"SELECT id FROM operators WHERE callsign={q(callsign)} LIMIT 1;").strip()
        if not found:
            psql(f"INSERT INTO operators (callsign, name) VALUES ({q(callsign)}, {q(name)});")
        return self.set_ham_cookie(callsign)
    def forgot(self, form):
        ensure_login()
        email = form.get("email", "").strip().lower()
        row = psql(f"SELECT callsign FROM site_accounts WHERE lower(email)={q(email)} LIMIT 1;").strip()
        if row:
            token = secrets.token_urlsafe(24)
            psql(f"UPDATE site_accounts SET reset_token={q(token)}, reset_until=now()+interval '2 hours' WHERE callsign={q(row)};")
            host = self.headers.get("Host", "127.0.0.1:8080")
            link = f"http://{host}/login.html?reset={token}"
            try:
                send_mail(email, "Пароль на портале радиолюбителей", "Чтобы задать новый пароль, откройте ссылку:\n\n" + link + "\n\nСсылка живёт два часа.")
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
        return self.send_json({"ok": True})
    def reset(self, form):
        ensure_login()
        token = form.get("token", "")
        password = form.get("password", "")
        if len(password) < 4 or not token:
            return self.send_json({"error": "Нужен новый пароль, хотя бы 4 знака."}, 400)
        row = psql(f"SELECT callsign FROM site_accounts WHERE reset_token={q(token)} AND reset_until>now() LIMIT 1;").strip()
        if not row:
            return self.send_json({"error": "Ссылка устарела. Запросите новую."}, 400)
        psql(f"UPDATE site_accounts SET password={q(hash_password(password))}, reset_token='', reset_until=NULL WHERE callsign={q(row)};")
        return self.set_ham_cookie(row)
    def chat_post(self):
        callsign = ham_from_cookie(self.headers.get("Cookie", ""))
        if not callsign:
            return self.send_json({"error": "Сначала войдите по позывному."}, 401)
        person = ham_profile(callsign)
        if not person:
            return self.send_json({"error": "Позывной не найден."}, 401)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(min(length, 8000))
        try:
            data = json.loads(raw.decode() or "{}")
        except Exception:
            return self.send_json({"error": "Не разобрал сообщение."}, 400)
        room = data.get("room") if data.get("room") in ("general", "table", "market") else "general"
        name = person["name"] or callsign
        text = str(data.get("text") or "").strip()[:1000]
        if not text:
            return self.send_json({"error": "Напишите текст."}, 400)
        ensure_chat()
        psql(f"INSERT INTO chat_messages (room, callsign, name, body) VALUES ({q(room)}, {q(callsign)}, {q(name)}, {q(text)});")
        row = psql("SELECT row_to_json(t)::text FROM (SELECT id, room, callsign, name, body AS text, to_char(created_at AT TIME ZONE 'UTC', 'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"') AS created_at FROM chat_messages ORDER BY id DESC LIMIT 1) t;")
        return self.send_json({"message": json.loads(row.strip())})
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/messages":
            return self.chat_post()
        form = self.read_form()
        if path in ("/api/enter", "/api/register", "/api/forgot", "/api/reset"):
            try:
                if path == "/api/enter": return self.enter(form)
                if path == "/api/register": return self.register(form)
                if path == "/api/forgot": return self.forgot(form)
                return self.reset(form)
            except Exception as exc:
                return self.send_json({"error": str(exc) or "ошибка сервера"}, 500)
        if path == "/api/logs/delete":
            if not self.cookie_ok(): return self.send_json({"error": "auth"}, 401)
            try:
                self.delete_log(form.get("id", ""))
            except Exception as exc:
                return self.send_json({"error": str(exc)}, 500)
            return self.send_json({"ok": True})
        if path == "/api/round/login":
            callsign = form.get("callsign", "").strip().upper()
            row = psql("SELECT COALESCE(callsign,''), COALESCE(password,'') FROM round_host WHERE id=1;").strip().split("|")
            if not row or row[0] != callsign or not row[1] or not hmac.compare_digest(form.get("password", "").encode(), row[1].encode()):
                return self.send_json({"error": "no"}, 403)
            return self.send_html("ok", extra=f"host={host_token()}; HttpOnly; Path=/")
        if path == "/api/round/host":
            if not self.cookie_ok(): return self.send_json({"error": "auth"}, 401)
            psql(f"UPDATE round_host SET callsign={q(form.get('callsign','').strip().upper())}, password={q(form.get('password',''))} WHERE id=1;")
            return self.send_json({"ok": True})
        if path == "/api/round/add":
            if not self.host_ok(): return self.send_json({"error": "auth"}, 401)
            callsign = form.get("callsign", "").strip().upper()
            name = psql(f"SELECT COALESCE(name,'') FROM operators WHERE callsign={q(callsign)} LIMIT 1;").strip()
            pos = psql("SELECT COALESCE(max(pos),0)+1 FROM round_queue;").strip() or "1"
            psql(f"INSERT INTO round_queue (pos, callsign, name) VALUES ({int(pos)}, {q(callsign)}, {q(name)});")
            return self.send_json({"ok": True})
        if path == "/api/round/next":
            if not self.host_ok(): return self.send_json({"error": "auth"}, 401)
            row = psql("SELECT id, callsign, COALESCE(name,'') FROM round_queue ORDER BY pos, id LIMIT 1;").strip()
            if not row: return self.send_json({"ok": True})
            item_id, callsign, name = row.split("|")
            psql(f"UPDATE round_host SET current_call={q(callsign)}, current_name={q(name)} WHERE id=1;")
            psql(f"DELETE FROM round_queue WHERE id={int(item_id)};")
            return self.send_json({"ok": True})
        if path.startswith("/api/qso"):
            my_call = form.get("my_call", "").strip().upper()
            dx_call = form.get("dx_call", "").strip().upper()
            when = form.get("when", "").replace("T", " ")
            band = form.get("band", "")
            lat = float(form.get("lat", "0") or 0)
            lon = float(form.get("lon", "0") or 0)
            locator = form.get("locator", "").strip()
            if not my_call or not dx_call or not when or not lat: return self.send_json({"error": "не заполнено"}, 400)
            psql(f"INSERT INTO contest_logs (my_call, dx_call, band, km, points, worked_at, my_lat, my_lon, my_locator) VALUES ({q(my_call)}, {q(dx_call)}, {q(band)}, 0, 0, {q(when)}, {lat}, {lon}, {q(locator)}) ON CONFLICT DO NOTHING;")
            return self.send_json({"ok": True})
        if path.startswith("/api/join") or path == "/login" or path.startswith("/api/operators"):
            if path == "/login":
                if not PASSWORD or not same_password(form.get("password", "")): return self.send_html("no", 403)
                return self.send_html("ok", extra=f"admin={token()}; HttpOnly; Path=/")
            callsign = form.get("callsign", "").strip().upper(); name = form.get("name", "").strip()
            if not callsign or not name: return self.send_json({"error": "нужны позывной и имя"}, 400)
            if path.startswith("/api/operators") and not self.cookie_ok(): return self.send_json({"error": "auth"}, 401)
            fields = dict(callsign=callsign, surname=form.get("surname",""), name=name, patronymic=form.get("patronymic",""), city=form.get("city",""), locator=form.get("locator","").upper(), phone=form.get("phone",""))
            found = psql(f"SELECT id FROM operators WHERE callsign={q(callsign)} LIMIT 1;").strip()
            row_id = form.get("id") or found
            if row_id:
                psql(f"UPDATE operators SET callsign={q(fields['callsign'])}, surname={q(fields['surname'])}, name={q(fields['name'])}, patronymic={q(fields['patronymic'])}, city={q(fields['city'])}, locator={q(fields['locator'])}, phone={q(fields['phone'])} WHERE id={int(row_id)};")
            else:
                psql(f"INSERT INTO operators (callsign, surname, name, patronymic, city, locator, phone) VALUES ({q(fields['callsign'])}, {q(fields['surname'])}, {q(fields['name'])}, {q(fields['patronymic'])}, {q(fields['city'])}, {q(fields['locator'])}, {q(fields['phone'])});")
            return self.send_json({"ok": True})
        self.send_error(404)
    def do_DELETE(self):
        path = self.path.split("?", 1)[0]
        qs = parse_qs(self.path.split("?", 1)[-1])
        if path.startswith("/api/round/remove"):
            if not self.host_ok(): return self.send_json({"error": "auth"}, 401)
            psql(f"DELETE FROM round_queue WHERE id={int(qs['id'][0])};")
            return self.send_json({"ok": True})
        if not self.cookie_ok(): return self.send_json({"error": "auth"}, 401)
        psql(f"DELETE FROM operators WHERE id={int(qs['id'][0])};")
        self.send_json({"ok": True})
    def log_message(self, fmt, *args): return

ADMIN_PAGE = r'''<!DOCTYPE html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Админка</title>
<style>body{font-family:system-ui,sans-serif;background:#faf7f4;margin:0;color:#1c1917}main{max-width:860px;margin:0 auto;padding:16px}h1{font-size:28px}form,.card{background:#fff;border-radius:16px;padding:16px;margin:12px 0}input,select{display:block;width:100%;box-sizing:border-box;padding:14px;margin:8px 0;font-size:18px;border:1px solid #e7e5e4;border-radius:12px}button{display:block;width:100%;box-sizing:border-box;background:#e85d04;color:#fff;border:0;border-radius:12px;padding:14px;font-size:18px;font-weight:700;margin-top:10px}button.ghost{background:#fff;color:#9a3412;border:1px solid #e85d04}.phone{display:grid;grid-template-columns:110px 1fr;gap:8px}.phone select,.phone input{margin:0}.hint{color:#78716c;margin:2px 0 0;font-size:13px}.err{color:#b91c1c}a{color:#e85d04}.log{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #f5f5f4}.log button{width:auto;margin:0;padding:8px 12px}.person{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #f5f5f4}.person button{width:auto;margin:0;padding:8px 10px;font-size:15px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}nav button{width:auto;margin:0;background:#fff;color:#1c1917;border:1px solid #e7e5e4}nav button.on{background:#e85d04;color:#fff;border-color:#e85d04}.hide{display:none}</style></head>
<body><main><p><a href="index.html">На портал</a></p><h1>Админка</h1>
<form id=login><input name=password type=password placeholder="Пароль админки" required><button>Войти</button><p class=err id=login-err></p></form>
<div id=app hidden>
<nav><button type=button class=on data-tab=people>Люди</button><button type=button data-tab=host>Ведущий</button><button type=button data-tab=logs>Связи</button></nav>
<section id=people><form id=edit><input name=id type=hidden><input name=phone type=hidden><input name=callsign placeholder=Позывной required><button class=ghost type=button onclick=lookup()>Найти на qrz.ru</button><p class=err id=qrz-err></p><input name=surname placeholder=Фамилия><input name=name placeholder=Имя required><input name=patronymic placeholder="Отчество, если есть"><input name=city placeholder=Город><select name=locator id=locator><option value="">Локатор</option></select><p class=hint>Телефон</p><div class=phone><select id=code><option value="+7">+7</option><option value="+375">+375</option><option value="+374">+374</option><option value="+995">+995</option><option value="+380">+380</option></select><input id=number inputmode=numeric placeholder="918 123-45-67" maxlength=13></div><button>Сохранить</button></form><div class=card id=cards></div></section>
<section id=host class=hide><form id=host-form><b>Ведущий круглого стола</b><input name=callsign list=host-calls placeholder="Позывной ведущего" autocomplete=off><datalist id=host-calls></datalist><input id=host-pass name=password placeholder="Пароль для ведущего"><button class=ghost type=button onclick=makePass()>Придумать пароль</button><button>Назначить ведущего</button></form></section>
<section id=logs class=hide><div class=card><b>Связи Кубка</b><div id=loglist></div><button class=ghost type=button onclick=clearLogs()>Удалить все связи</button></div></section>
</div></main>
<script>
const login=document.getElementById('login'), app=document.getElementById('app');
function mask(v){const d=v.replace(/\D/g,'').slice(0,10); let s=d.slice(0,3); if(d.length>3)s+=' '+d.slice(3,6); if(d.length>6)s+='-'+d.slice(6,8); if(d.length>8)s+='-'+d.slice(8,10); return s;}
document.getElementById('number').addEventListener('input', e=>{e.target.value=mask(e.target.value);});
function makePass(){const words=['море','волна','маяк','гора','чайка','эфир','солнце','антенна']; const pass=words[Math.floor(Math.random()*words.length)]+'-'+String(Math.floor(Math.random()*90)+10); const field=document.getElementById('host-pass'); field.value=pass; field.type='text';}
function showTab(name){document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on', b.dataset.tab===name)); ['people','host','logs'].forEach(id=>document.getElementById(id).classList.toggle('hide', id!==name));}
document.querySelectorAll('nav button').forEach(b=>b.onclick=()=>showTab(b.dataset.tab));
async function api(url, opts){const res=await fetch(url, opts); if(res.status==401){app.hidden=true; login.hidden=false; throw new Error('auth');} const data=await res.json(); if(!res.ok) throw new Error(data.error||'ошибка'); return data;}
async function loadLocators(){const list=await api('/api/locators'); const sel=document.getElementById('locator'); const cur=sel.value; sel.innerHTML='<option value="">Локатор</option>'+list.map(x=>'<option value="'+x.code+'">'+x.code+' — '+x.title+'</option>').join(''); sel.value=cur;}
async function loadHostCalls(){const calls=await api('/api/calls'); document.getElementById('host-calls').innerHTML=calls.map(p=>'<option value="'+p.callsign+'">'+p.callsign+' '+(p.name||'')+'</option>').join('');}
async function loadLogs(){const rows=await api('/api/logs'); document.getElementById('loglist').innerHTML=rows.map(r=>'<div class=log><span>'+r.worked_at+' '+r.my_call+' — '+r.dx_call+' · '+r.band+'</span><button class=ghost type=button onclick="delLog('+r.id+')">Удалить</button></div>').join('')||'<p>Пока нет связей</p>';}
async function delLog(id){if(!confirm('Удалить эту связь?')) return; try{await api('/api/logs/delete',{method:'POST', body:new URLSearchParams({id})}); loadLogs();}catch(e){alert(e.message);}}
async function clearLogs(){if(!confirm('Удалить все связи Кубка?')) return; try{await api('/api/logs/delete',{method:'POST', body:new URLSearchParams({id:'all'})}); loadLogs();}catch(e){alert(e.message);}}
login.onsubmit=async(e)=>{e.preventDefault(); const res=await fetch('/login',{method:'POST', body:new URLSearchParams(new FormData(login))}); if(!res.ok){document.getElementById('login-err').textContent='Неверный пароль'; return;} login.hidden=true; app.hidden=false; await loadLocators(); await loadHostCalls(); load(); loadLogs();};
document.getElementById('host-form').onsubmit=async(e)=>{e.preventDefault(); await api('/api/round/host',{method:'POST', body:new URLSearchParams(new FormData(e.target))}); alert('Ведущий назначен. Пароль: '+document.getElementById('host-pass').value);};
async function load(){const people=await api('/api/operators'); document.getElementById('cards').innerHTML=people.map(p=>'<div class=person><div><b>'+p.callsign+'</b> '+(p.surname||'')+' '+p.name+' '+(p.patronymic||'')+'<div class=hint>'+[p.city,p.locator,p.phone].filter(Boolean).join(' · ')+'</div></div><button type=button onclick=\'fill('+JSON.stringify(p)+')\'>Правка</button><button class=ghost type=button onclick=del('+p.id+')>Удалить</button></div>').join('')||'<p>Пока никого нет</p>';}
function fill(p){const f=document.getElementById('edit'); for (const k of ['id','callsign','surname','name','patronymic','city','locator']) f[k].value=p[k]||''; const digits=(p.phone||'').replace(/\D/g,''); const code=['+375','+374','+995','+380','+7'].find(c=>digits.startsWith(c.slice(1)))||'+7'; document.getElementById('code').value=code; document.getElementById('number').value=mask(digits.slice(code.length-1)); showTab('people'); window.scrollTo(0,0);}
async function lookup(){document.getElementById('qrz-err').textContent=''; const res=await fetch('/api/qrz?call='+encodeURIComponent(document.getElementById('edit').callsign.value)); const data=await res.json(); if(!res.ok){document.getElementById('qrz-err').textContent=data.error||'Не нашлось'; return;} fill(data);}
document.getElementById('edit').onsubmit=async(e)=>{e.preventDefault(); const digits=document.getElementById('number').value.replace(/\D/g,''); e.target.phone.value=digits?document.getElementById('code').value+' '+document.getElementById('number').value:''; await api('/api/operators',{method:'POST', body:new URLSearchParams(new FormData(e.target))}); e.target.reset(); document.getElementById('number').value=''; load();};
async function del(id){if(!confirm('Удалить?')) return; await api('/api/operators?id='+id,{method:'DELETE'}); load();}
</script></body></html>'''

if __name__ == "__main__":
    ensure_round()
    print(f"Портал: http://{HOST}:{PORT}")
    print(f"Админка: http://{HOST}:{PORT}/admin")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
