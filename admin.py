#!/usr/bin/env python3
import hashlib, hmac, json, os, secrets, subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
HOST = os.environ.get("ADMIN_HOST", "127.0.0.1")
PORT = int(os.environ.get("ADMIN_PORT", "8081"))
PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
DB = os.environ.get("PGDATABASE", "sochiham")
DB_USER = os.environ.get("PGUSER", "sochiham")
DB_PASSWORD = os.environ.get("PGPASSWORD", "")
SECRET = os.environ.get("ADMIN_SECRET", secrets.token_hex(16))

def token():
    return hmac.new(SECRET.encode(), b"admin-ok", hashlib.sha256).hexdigest()

def same_password(given):
    return hmac.compare_digest(given.encode(), PASSWORD.encode())

def psql(sql):
    env = os.environ.copy()
    if DB_PASSWORD:
        env["PGPASSWORD"] = DB_PASSWORD
    proc = subprocess.run(["psql", "-h", "127.0.0.1", "-U", DB_USER, "-d", DB, "-v", "ON_ERROR_STOP=1", "-t", "-A", "-c", sql], capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "ошибка базы")
    return proc.stdout

def q(value):
    return "'" + str(value).replace("'", "''") + "'"

PAGE = """<!DOCTYPE html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Люди</title>
<style>body{font-family:system-ui,sans-serif;background:#faf7f4;margin:0;color:#1c1917}main{max-width:880px;margin:0 auto;padding:16px}form{background:#fff;border-radius:16px;padding:16px;margin:12px 0}input{width:100%;padding:12px;margin:6px 0;font-size:16px;border:1px solid #e7e5e4;border-radius:12px}button{background:#e85d04;color:#fff;border:0;border-radius:12px;padding:12px 16px;font-weight:700}button.ghost{background:#fff;color:#b91c1c;border:1px solid #b91c1c}table{width:100%;border-collapse:collapse;background:#fff}td,th{text-align:left;padding:10px;border-bottom:1px solid #f5f5f4}.err{color:#b91c1c}</style></head>
<body><main><h1>Радиолюбители</h1>
<form id=login><input name=password type=password placeholder="Пароль админки" required><button>Войти</button><p class=err id=login-err></p></form>
<div id=app hidden><form id=edit><input name=id type=hidden><input name=callsign placeholder=Позывной required><input name=surname placeholder=Фамилия><input name=name placeholder=Имя required><input name=patronymic placeholder="Отчество, если есть"><input name=city placeholder=Город><input name=locator placeholder=Локатор><input name=phone placeholder=Телефон><button>Сохранить</button></form>
<table><thead><tr><th>Позывной</th><th>Фамилия</th><th>Имя</th><th>Отчество</th><th>Город</th><th></th></tr></thead><tbody id=rows></tbody></table></div></main>
<script>
const login=document.getElementById('login'), app=document.getElementById('app');
async function api(url, opts){const res=await fetch(url, opts); if(res.status==401){app.hidden=true; login.hidden=false; throw new Error('auth');} return res.json();}
login.onsubmit=async(e)=>{e.preventDefault(); const res=await fetch('/login',{method:'POST', body:new URLSearchParams(new FormData(login))}); if(!res.ok){document.getElementById('login-err').textContent='Неверный пароль'; return;} login.hidden=true; app.hidden=false; load();};
async function load(){const people=await api('/api/operators'); document.getElementById('rows').innerHTML=people.map(p=>`<tr><td>${p.callsign}</td><td>${p.surname||''}</td><td>${p.name}</td><td>${p.patronymic||''}</td><td>${p.city||''}</td><td><button type=button onclick='fill(${JSON.stringify(p)})'>Исправить</button> <button class=ghost type=button onclick=del(${p.id})>Удалить</button></td></tr>`).join('');}
function fill(p){const f=document.getElementById('edit'); for (const k of ['id','callsign','surname','name','patronymic','city','locator','phone']) f[k].value=p[k]||'';}
document.getElementById('edit').onsubmit=async(e)=>{e.preventDefault(); await api('/api/operators',{method:'POST', body:new URLSearchParams(new FormData(e.target))}); e.target.reset(); load();};
async function del(id){if(!confirm('Удалить?')) return; await api('/api/operators?id='+id,{method:'DELETE'}); load();}
</script></body></html>"""

class Handler(BaseHTTPRequestHandler):
    def cookie_ok(self):
        return f"admin={token()}" in self.headers.get("Cookie", "").split("; ")
    def send(self, code, body, content="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", content)
        self.send_header("Content-Length", str(len(data)))
        if extra:
            self.send_header("Set-Cookie", extra)
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path.startswith("/api/operators"):
            if not self.cookie_ok():
                return self.send(401, '{"error":"auth"}', "application/json")
            rows = psql("SELECT id, callsign, COALESCE(surname,''), name, COALESCE(patronymic,''), COALESCE(city,''), COALESCE(locator,''), COALESCE(phone,'') FROM operators ORDER BY callsign;")
            people = []
            for line in rows.splitlines():
                if not line.strip():
                    continue
                parts = line.split("|")
                people.append(dict(zip(["id","callsign","surname","name","patronymic","city","locator","phone"], parts)))
            return self.send(200, json.dumps(people, ensure_ascii=False), "application/json")
        self.send(200, PAGE)
    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode()
        return {k: v[0] for k, v in parse_qs(raw).items()}
    def do_POST(self):
        form = self.read_form()
        if self.path == "/login":
            if not PASSWORD or not same_password(form.get("password", "")):
                return self.send(403, "no")
            return self.send(200, "ok", extra=f"admin={token()}; HttpOnly; Path=/")
        if not self.cookie_ok():
            return self.send(401, "auth")
        callsign = form.get("callsign", "").strip().upper()
        name = form.get("name", "").strip()
        if not callsign or not name:
            return self.send(400, "нужны позывной и имя")
        fields = dict(callsign=callsign, surname=form.get("surname",""), name=name, patronymic=form.get("patronymic",""), city=form.get("city",""), locator=form.get("locator",""), phone=form.get("phone",""))
        if form.get("id"):
            psql(f"UPDATE operators SET callsign={q(fields['callsign'])}, surname={q(fields['surname'])}, name={q(fields['name'])}, patronymic={q(fields['patronymic'])}, city={q(fields['city'])}, locator={q(fields['locator'])}, phone={q(fields['phone'])} WHERE id={int(form['id'])};")
        else:
            psql(f"INSERT INTO operators (callsign, surname, name, patronymic, city, locator, phone) VALUES ({q(fields['callsign'])}, {q(fields['surname'])}, {q(fields['name'])}, {q(fields['patronymic'])}, {q(fields['city'])}, {q(fields['locator'])}, {q(fields['phone'])});")
        self.send(200, '{"ok":true}', "application/json")
    def do_DELETE(self):
        if not self.cookie_ok():
            return self.send(401, "auth")
        qs = parse_qs(self.path.split("?", 1)[-1])
        psql(f"DELETE FROM operators WHERE id={int(qs['id'][0])};")
        self.send(200, '{"ok":true}', "application/json")
    def log_message(self, fmt, *args):
        return

if __name__ == "__main__":
    if not PASSWORD:
        raise SystemExit("Задайте пароль: ADMIN_PASSWORD='секрет' python3 admin.py")
    print(f"Админка: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
