#!/usr/bin/env python3
import hashlib, hmac, json, os, secrets, smtplib, subprocess, xml.etree.ElementTree as ET
from email.header import Header
from email.mime.text import MIMEText
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode
from pathlib import Path
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
    for col, kind in (
        ("surname", "text DEFAULT ''"),
        ("patronymic", "text DEFAULT ''"),
        ("city", "text DEFAULT ''"),
        ("locator", "text DEFAULT ''"),
        ("phone", "text DEFAULT ''"),
        ("about", "text DEFAULT ''"),
        ("birth_year", "integer"),
        ("birth_date", "date"),
        ("show_phone", "boolean DEFAULT false"),
        ("show_birth", "boolean DEFAULT false"),
    ):
        psql(f"ALTER TABLE site_accounts ADD COLUMN IF NOT EXISTS {col} {kind};")

def profile_view(callsign):
    ensure_login()
    row = psql(f"""SELECT a.callsign, COALESCE(a.name,''), COALESCE(a.email,''),
      COALESCE(NULLIF(a.surname,''), o.surname, ''),
      COALESCE(NULLIF(a.patronymic,''), o.patronymic, ''),
      COALESCE(NULLIF(a.city,''), o.city, ''),
      COALESCE(NULLIF(a.locator,''), o.locator, ''),
      COALESCE(NULLIF(a.phone,''), o.phone, ''),
      COALESCE(NULLIF(a.about,''), o.about, ''),
      COALESCE(a.birth_date::text, ''),
      CASE WHEN a.show_phone THEN '1' ELSE '' END,
      CASE WHEN a.show_birth THEN '1' ELSE '' END
      FROM site_accounts a
      LEFT JOIN operators o ON o.callsign=a.callsign
      WHERE a.callsign={q(callsign)} LIMIT 1;""").strip()
    if not row:
        return None
    bits = row.split("|")
    keys = ["callsign","name","email","surname","patronymic","city","locator","phone","about","birth_date","show_phone","show_birth"]
    data = dict(zip(keys, bits + [""]*len(keys)))
    data["show_phone"] = bool(data["show_phone"])
    data["show_birth"] = bool(data["show_birth"])
    return data

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
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com").strip() or "smtp.gmail.com"
    user = os.environ.get("SMTP_USER", "").strip()
    password = os.environ.get("SMTP_PASSWORD", "").replace(" ", "")
    sender = os.environ.get("SMTP_FROM", user).strip()
    if not user or not password:
        raise RuntimeError("В qrz.env нет адреса или пароля приложения.")
    msg = MIMEText(body, "plain", "utf-8")
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = Header(subject, "utf-8")
    raw = msg.as_string()
    try:
        smtp = smtplib.SMTP_SSL(host, 465, timeout=30)
        smtp.login(user, password)
        smtp.sendmail(sender, [to], raw)
        smtp.quit()
    except Exception as exc:
        text = str(exc)
        if "535" in text or "Authentication" in text or "Username and Password" in text:
            raise RuntimeError("Gmail не принял пароль. Нужны 16 букв пароля приложения, без пробелов.")
        if "connect" in text or "closed" in text or "timed out" in text:
            raise RuntimeError("Сервер не смог связаться с Gmail. Проверьте интернет на сервере и пароль приложения.")
        raise RuntimeError(text)

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


PHOTOS_FILE = Path(__file__).resolve().parent / "data" / "photos.json"

def load_album():
    if PHOTOS_FILE.exists():
        try:
            data = json.loads(PHOTOS_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("items"), list):
                return data
        except Exception:
            pass
    return {"title": "Фото", "subtitle": "", "note": "", "items": []}

def save_album(data):
    PHOTOS_FILE.parent.mkdir(exist_ok=True)
    clean = []
    for item in data.get("items") or []:
        file = str(item.get("file") or "").replace("\\", "/").lstrip("/")
        if not file.startswith("photos/") or ".." in file:
            continue
        path = Path(__file__).resolve().parent / file
        if not path.is_file():
            continue
        clean.append({"file": file, "title": str(item.get("title") or "Снимок")[:80]})
    out = {
        "title": str(data.get("title") or "Фото")[:80],
        "subtitle": str(data.get("subtitle") or "")[:120],
        "note": str(data.get("note") or "")[:500],
        "items": clean,
    }
    PHOTOS_FILE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return out

def drop_removed_photos(old, new):
    keep = {item["file"] for item in new.get("items") or []}
    root = Path(__file__).resolve().parent
    for item in old.get("items") or []:
        file = item.get("file") or ""
        if file in keep or not file.startswith("photos/") or ".." in file:
            continue
        path = root / file
        if path.is_file():
            path.unlink()

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        path = self.path.split("?", 1)[0]
        if path.endswith(".html") or path in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-store")
        super().end_headers()
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
        if path == "/api/photos":
            return self.send_json(load_album())
        if path == "/api/round":
            return self.send_json(round_view())
        if path == "/api/logout":
            self.send_response(302)
            self.send_header("Location", "/index.html")
            self.send_header("Set-Cookie", "ham=; HttpOnly; Path=/; Max-Age=0")
            self.send_header("Set-Cookie", "ham_call=; Path=/; Max-Age=0")
            self.end_headers()
            return
        if path == "/api/me":
            call = ham_from_cookie(self.headers.get("Cookie", ""))
            if not call:
                return self.send_json({"callsign": ""})
            person = profile_view(call) or {"callsign": call, "name": ""}
            return self.send_json(person)
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
            ensure_login()
            rows = psql("""SELECT o.callsign, COALESCE(o.name,''), COALESCE(o.city,''), COALESCE(o.locator,''),
              CASE WHEN a.show_phone THEN COALESCE(NULLIF(a.phone,''), o.phone, '') ELSE '' END,
              CASE WHEN a.show_birth THEN COALESCE(a.birth_year::text,'') ELSE '' END
              FROM operators o LEFT JOIN site_accounts a ON a.callsign=o.callsign ORDER BY o.callsign;""")
            return self.send_json([dict(zip(["callsign","name","city","locator","phone","birth_year"], line.split("|"))) for line in rows.splitlines() if line.strip()])
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
    def photos_save(self):
        if not self.cookie_ok():
            return self.send_json({"error": "auth"}, 401)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(min(length, 200000))
        try:
            data = json.loads(raw.decode() or "{}")
        except Exception:
            return self.send_json({"error": "Не разобрал список фото."}, 400)
        old = load_album()
        saved = save_album(data)
        drop_removed_photos(old, saved)
        return self.send_json(saved)
    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/messages":
            return self.chat_post()
        if path == "/api/admin/photos":
            return self.photos_save()
        form = self.read_form()
        if path == "/api/profile":
            call = ham_from_cookie(self.headers.get("Cookie", ""))
            if not call:
                return self.send_json({"error": "Сначала войдите."}, 401)
            ensure_login()
            name = form.get("name", "").strip()[:40]
            email = form.get("email", "").strip().lower()[:80]
            surname = form.get("surname", "").strip()[:40]
            patronymic = form.get("patronymic", "").strip()[:40]
            city = form.get("city", "").strip()[:40]
            locator = form.get("locator", "").strip().upper()[:6]
            phone = form.get("phone", "").strip()[:20]
            about = form.get("about", "").strip()[:300]
            birth = form.get("birth_date", "").strip()
            show_phone = "true" if form.get("show_phone") else "false"
            show_birth = "true" if form.get("show_birth") else "false"
            if not name or "@" not in email:
                return self.send_json({"error": "Нужны имя и почта."}, 400)
            year_sql = "NULL"
            date_sql = "NULL"
            if birth:
                parts = birth.replace("/", ".").replace("-", ".").split(".")
                if len(parts)==3 and len(parts[0])==4:
                    birth = f"{parts[0]}-{parts[1]}-{parts[2]}"
                elif len(parts)==3:
                    birth = f"{parts[2]}-{parts[1]}-{parts[0]}"
                try:
                    y, m, d = [int(x) for x in birth.split("-")]
                    if not 1920 <= y <= 2026:
                        raise ValueError
                except Exception:
                    return self.send_json({"error": "Дата рождения: день, месяц и год, например 15.04.1970."}, 400)
                year_sql = str(y)
                date_sql = q(f"{y:04d}-{m:02d}-{d:02d}")
            taken = psql(f"SELECT callsign FROM site_accounts WHERE lower(email)={q(email)} AND callsign<>{q(call)} LIMIT 1;").strip()
            if taken:
                return self.send_json({"error": "Эта почта уже занята."}, 409)
            try:
                psql(f"""UPDATE site_accounts SET name={q(name)}, email={q(email)}, surname={q(surname)}, patronymic={q(patronymic)}, city={q(city)}, locator={q(locator)}, phone={q(phone)}, about={q(about)}, birth_year={year_sql}, birth_date={date_sql}, show_phone={show_phone}, show_birth={show_birth} WHERE callsign={q(call)};""")
            except Exception as exc:
                return self.send_json({"error": str(exc) or "Не сохранилось"}, 500)
            try:
                psql(f"""UPDATE operators SET name={q(name)}, surname={q(surname)}, patronymic={q(patronymic)}, city={q(city)}, locator={q(locator)}, phone={q(phone)}, about={q(about)} WHERE callsign={q(call)};""")
            except Exception:
                pass
            return self.send_json({"ok": True})
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
<style>body{font-family:system-ui,sans-serif;background:#faf7f4;margin:0;color:#1c1917}main{max-width:860px;margin:0 auto;padding:16px}h1{font-size:28px}form,.card{background:#fff;border-radius:16px;padding:16px;margin:12px 0}input,select{display:block;width:100%;box-sizing:border-box;padding:14px;margin:8px 0;font-size:18px;border:1px solid #e7e5e4;border-radius:12px}button{display:block;width:100%;box-sizing:border-box;background:#e85d04;color:#fff;border:0;border-radius:12px;padding:14px;font-size:18px;font-weight:700;margin-top:10px}button.ghost{background:#fff;color:#9a3412;border:1px solid #e85d04}.phone{display:grid;grid-template-columns:110px 1fr;gap:8px}.phone select,.phone input{margin:0}.hint{color:#78716c;margin:2px 0 0;font-size:13px}.err{color:#b91c1c}a{color:#e85d04}.log{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #f5f5f4}.log button{width:auto;margin:0;padding:8px 12px}.person{display:grid;grid-template-columns:1fr auto auto;gap:8px;align-items:center;padding:8px 0;border-bottom:1px solid #f5f5f4}.person button{width:auto;margin:0;padding:8px 10px;font-size:15px}nav{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}nav button{width:auto;margin:0;background:#fff;color:#1c1917;border:1px solid #e7e5e4}nav button.on{background:#e85d04;color:#fff;border-color:#e85d04}.hide{display:none}.shotrow{padding:16px 0;border-bottom:1px solid #f5f5f4}.shotrow img{width:100%;max-height:420px;object-fit:contain;background:#f5f5f4;border-radius:12px;display:block}.shotbtns{display:flex;gap:8px}.shotbtns button{width:auto;flex:1;margin:0}.viewer{position:fixed;inset:0;background:rgba(10,12,11,.92);z-index:30;display:flex;align-items:center;justify-content:center}.viewer.hide{display:none}.viewer img{max-width:88vw;max-height:84vh;object-fit:contain}.viewer .navbtn{position:absolute;width:auto;background:transparent;color:#fff;border:0;font-size:48px;padding:8px 16px}.viewer .vx{top:10px;right:12px;font-size:16px}.viewer .vprev{left:4px}.viewer .vnext{right:4px}.viewer .vdel{bottom:18px;left:50%;transform:translateX(-50%);font-size:18px;background:#e85d04;border-radius:12px;padding:10px 22px}#view-cap{position:absolute;bottom:64px;color:#fff;margin:0}</style></head>
<body><main><p><a href="index.html">На портал</a></p><h1>Админка</h1>
<form id=login><input name=password type=password placeholder="Пароль админки" required><button>Войти</button><p class=err id=login-err></p></form>
<div id=app hidden>
<nav><button type=button class=on data-tab=people>Люди</button><button type=button data-tab=host>Ведущий</button><button type=button data-tab=logs>Связи</button><button type=button data-tab=photos>Фото</button></nav>
<section id=people><form id=edit><input name=id type=hidden><input name=phone type=hidden><input name=callsign placeholder=Позывной required><button class=ghost type=button onclick=lookup()>Найти на qrz.ru</button><p class=err id=qrz-err></p><input name=surname placeholder=Фамилия><input name=name placeholder=Имя required><input name=patronymic placeholder="Отчество, если есть"><input name=city placeholder=Город><select name=locator id=locator><option value="">Локатор</option></select><p class=hint>Телефон</p><div class=phone><select id=code><option value="+7">+7</option><option value="+375">+375</option><option value="+374">+374</option><option value="+995">+995</option><option value="+380">+380</option></select><input id=number inputmode=numeric placeholder="918 123-45-67" maxlength=13></div><button>Сохранить</button></form><div class=card id=cards></div></section>
<section id=host class=hide><form id=host-form><b>Ведущий круглого стола</b><input name=callsign list=host-calls placeholder="Позывной ведущего" autocomplete=off><datalist id=host-calls></datalist><input id=host-pass name=password placeholder="Пароль для ведущего"><button class=ghost type=button onclick=makePass()>Придумать пароль</button><button>Назначить ведущего</button></form></section>
<section id=logs class=hide><div class=card><b>Связи Кубка</b><div id=loglist></div><button class=ghost type=button onclick=clearLogs()>Удалить все связи</button></div></section><section id=photos class=hide><form id=photo-form><b>Альбом</b><input id=photo-title placeholder=Название><input id=photo-sub placeholder=Подзаголовок><textarea id=photo-note placeholder=Подпись rows=3></textarea><div id=photo-list></div><button>Сохранить фото</button></form></section>
</div></main><div id=viewer class="viewer hide" style="display:none"><button type=button class="navbtn vx" onclick="closeView()">Закрыть</button><button type=button class="navbtn vprev" onclick="stepView(-1)">‹</button><img id=view-img alt=""><button type=button class="navbtn vnext" onclick="stepView(1)">›</button><button type=button class="navbtn vdel" onclick="dropPhoto(window.shotAt)">Удалить</button><p id=view-cap></p></div>
<script>
const login=document.getElementById('login'), app=document.getElementById('app');
function mask(v){const d=v.replace(/\D/g,'').slice(0,10); let s=d.slice(0,3); if(d.length>3)s+=' '+d.slice(3,6); if(d.length>6)s+='-'+d.slice(6,8); if(d.length>8)s+='-'+d.slice(8,10); return s;}
document.getElementById('number').addEventListener('input', e=>{e.target.value=mask(e.target.value);});
function makePass(){const words=['море','волна','маяк','гора','чайка','эфир','солнце','антенна']; const pass=words[Math.floor(Math.random()*words.length)]+'-'+String(Math.floor(Math.random()*90)+10); const field=document.getElementById('host-pass'); field.value=pass; field.type='text';}
function showTab(name){document.querySelectorAll('nav button').forEach(b=>b.classList.toggle('on', b.dataset.tab===name)); ['people','host','logs','photos'].forEach(id=>document.getElementById(id).classList.toggle('hide', id!==name)); if(name==='photos') loadPhotos();}
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
let photoAlbum={items:[]};
function esc(s){return String(s||'').replace(/&/g,'&').replace(/</g,'<').replace(/"/g,'"');}
async function loadPhotos(){photoAlbum=await api('/api/photos'); document.getElementById('photo-title').value=photoAlbum.title||''; document.getElementById('photo-sub').value=photoAlbum.subtitle||''; document.getElementById('photo-note').value=photoAlbum.note||''; drawPhotos();}
function drawPhotos(){document.getElementById('photo-list').innerHTML=(photoAlbum.items||[]).map((p,i)=>'<div class=shotrow><img src="/'+p.file+'" alt="" onclick="openView('+i+')" style="cursor:pointer"><input value="'+esc(p.title)+'" oninput="photoAlbum.items['+i+'].title=this.value"><div class=shotbtns><button type=button onclick="movePhoto('+i+',-1)">Выше</button><button type=button onclick="movePhoto('+i+',1)">Ниже</button><button class=ghost type=button onclick="dropPhoto('+i+')">Удалить</button></div></div>').join('')||'<p>Фото нет</p>';}
function openView(i){const items=photoAlbum.items||[]; if(!items.length) return; window.shotAt=(i+items.length)%items.length; const s=items[window.shotAt]; document.getElementById('view-img').src='/'+s.file; document.getElementById('view-cap').textContent=(s.title||'')+' · '+(window.shotAt+1)+' / '+items.length; const v=document.getElementById('viewer'); v.classList.remove('hide'); v.style.display='flex';}
function closeView(){const v=document.getElementById('viewer'); v.classList.add('hide'); v.style.display='none';}
function stepView(d){openView((window.shotAt||0)+d);}
document.addEventListener('keydown', e=>{const v=document.getElementById('viewer'); if(!v||v.classList.contains('hide')) return; if(e.key==='Escape') closeView(); if(e.key==='ArrowLeft') stepView(-1); if(e.key==='ArrowRight') stepView(1); if(e.key==='Delete') dropPhoto(window.shotAt);});
function movePhoto(i,dir){const j=i+dir; if(j<0||j>=photoAlbum.items.length) return; const a=photoAlbum.items; [a[i],a[j]]=[a[j],a[i]]; drawPhotos();}
async function dropPhoto(i){if(i<0||i>=photoAlbum.items.length) return; photoAlbum.items.splice(i,1); drawPhotos(); const stay=document.getElementById('viewer').style.display==='flex'; await savePhotos(false); if(!stay) return; if(!photoAlbum.items.length) closeView(); else openView(Math.min(i, photoAlbum.items.length-1));}
async function savePhotos(tell){photoAlbum.title=document.getElementById('photo-title').value; photoAlbum.subtitle=document.getElementById('photo-sub').value; photoAlbum.note=document.getElementById('photo-note').value; const res=await fetch('/api/admin/photos',{method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(photoAlbum)}); if(!res.ok){alert('Не сохранилось'); return;} photoAlbum=await res.json(); drawPhotos(); if(tell) alert('Фото сохранены');}
document.getElementById('photo-form').onsubmit=async(e)=>{e.preventDefault(); await savePhotos(true);};

</script></body></html>'''

if __name__ == "__main__":
    ensure_round()
    print(f"Портал: http://{HOST}:{PORT}")
    print(f"Админка: http://{HOST}:{PORT}/admin")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
