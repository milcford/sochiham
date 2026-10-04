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
def open_json(self, payload, code=200):
    body = json.dumps(payload, ensure_ascii=False).encode()
    self.send_response(code)
    self.send_header("Content-Type", "application/json")
    self.send_header("Access-Control-Allow-Origin", "*")
    self.send_header("Content-Length", str(len(body)))
    self.end_headers(); self.wfile.write(body)
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

PAGE = r'''<!DOCTYPE html><html lang=ru><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><title>Люди</title>
<style>body{font-family:system-ui,sans-serif;background:#faf7f4;margin:0;color:#1c1917}main{max-width:720px;margin:0 auto;padding:16px}h1{font-size:28px}form{background:#fff;border-radius:16px;padding:16px;margin:12px 0}input,select{display:block;width:100%;box-sizing:border-box;padding:14px;margin:8px 0;font-size:18px;border:1px solid #e7e5e4;border-radius:12px}button{display:block;width:100%;box-sizing:border-box;background:#e85d04;color:#fff;border:0;border-radius:12px;padding:14px;font-size:18px;font-weight:700;margin-top:10px}button.ghost{background:#fff;color:#9a3412;border:1px solid #e85d04}.phone{display:grid;grid-template-columns:110px 1fr;gap:8px}.phone select,.phone input{margin:0}.hint{color:#78716c;margin:8px 0 0}.card{background:#fff;border-radius:16px;padding:14px;margin:10px 0}.card .actions{display:grid;grid-template-columns:1fr 1fr;gap:8px}.err{color:#b91c1c}</style></head>
<body><main><h1>Радиолюбители</h1>
<form id=login><input name=password type=password placeholder="Пароль админки" required><button>Войти</button><p class=err id=login-err></p></form>
<div id=app hidden><form id=edit><input name=id type=hidden><input name=phone type=hidden><input name=callsign placeholder=Позывной required><button class=ghost type=button onclick=lookup()>Найти на qrz.ru</button><p class=err id=qrz-err></p><input name=surname placeholder=Фамилия><input name=name placeholder=Имя required><input name=patronymic placeholder="Отчество, если есть"><input name=city placeholder=Город><select name=locator id=locator><option value="">Локатор</option></select><p class=hint>Телефон</p><div class=phone><select id=code><option value="+7">+7</option><option value="+375">+375</option><option value="+374">+374</option><option value="+995">+995</option><option value="+380">+380</option></select><input id=number inputmode=numeric placeholder="918 123-45-67" maxlength=13></div><button>Сохранить</button></form>
<div id=cards></div></div></main>
<script>
const login=document.getElementById('login'), app=document.getElementById('app');
function mask(v){const d=v.replace(/\D/g,'').slice(0,10); let s=d.slice(0,3); if(d.length>3)s+=' '+d.slice(3,6); if(d.length>6)s+='-'+d.slice(6,8); if(d.length>8)s+='-'+d.slice(8,10); return s;}
document.getElementById('number').addEventListener('input', e=>{e.target.value=mask(e.target.value);});
async function api(url, opts){const res=await fetch(url, opts); if(res.status==401){app.hidden=true; login.hidden=false; throw new Error('auth');} return res.json();}
async function loadLocators(){const list=await api('/api/locators'); const sel=document.getElementById('locator'); const cur=sel.value; sel.innerHTML='<option value="">Локатор</option>'+list.map(x=>'<option value="'+x.code+'">'+x.code+' — '+x.title+'</option>').join(''); sel.value=cur;}
login.onsubmit=async(e)=>{e.preventDefault(); const res=await fetch('/login',{method:'POST', body:new URLSearchParams(new FormData(login))}); if(!res.ok){document.getElementById('login-err').textContent='Неверный пароль'; return;} login.hidden=true; app.hidden=false; await loadLocators(); load();};
async function load(){const people=await api('/api/operators'); document.getElementById('cards').innerHTML=people.map(p=>'<div class=card><b>'+p.callsign+'</b><div>'+(p.surname||'')+' '+p.name+' '+(p.patronymic||'')+'</div><div>'+(p.city||'')+' '+(p.locator||'')+'</div><div>'+(p.phone||'')+'</div><div class=actions><button type=button onclick=\'fill('+JSON.stringify(p)+')\'>Исправить</button><button class=ghost type=button onclick=del('+p.id+')>Удалить</button></div></div>').join('');}
function fill(p){const f=document.getElementById('edit'); for (const k of ['id','callsign','surname','name','patronymic','city','locator']) f[k].value=p[k]||''; const digits=(p.phone||'').replace(/\D/g,''); const code=['+375','+374','+995','+380','+7'].find(c=>digits.startsWith(c.slice(1)))||'+7'; document.getElementById('code').value=code; document.getElementById('number').value=mask(digits.slice(code.length-1)); window.scrollTo(0,0);}
async function lookup(){document.getElementById('qrz-err').textContent=''; try { fill(await api('/api/qrz?call='+encodeURIComponent(document.getElementById('edit').callsign.value)));} catch(e){ document.getElementById('qrz-err').textContent='Не нашлось'; }}
document.getElementById('edit').onsubmit=async(e)=>{e.preventDefault(); const digits=document.getElementById('number').value.replace(/\D/g,''); e.target.phone.value=digits?document.getElementById('code').value+' '+document.getElementById('number').value:''; await api('/api/operators',{method:'POST', body:new URLSearchParams(new FormData(e.target))}); e.target.reset(); document.getElementById('number').value=''; load();};
async function del(id){if(!confirm('Удалить?')) return; await api('/api/operators?id='+id,{method:'DELETE'}); load();}
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    def cookie_ok(self):
        return f"admin={token()}" in self.headers.get("Cookie", "").split("; ")
    def send(self, code, body, content="text/html; charset=utf-8", extra=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(code); self.send_header("Content-Type", content); self.send_header("Content-Length", str(len(data)))
        if extra: self.send_header("Set-Cookie", extra)
        self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path.startswith("/api/qso"):
            rows = psql("SELECT a.my_call, a.dx_call, a.band, to_char(a.worked_at, 'DD.MM HH24:MI'), COALESCE(round(2*6371*asin(sqrt(power(sin(radians(b.my_lat-a.my_lat)/2),2)+cos(radians(a.my_lat))*cos(radians(b.my_lat))*power(sin(radians(b.my_lon-a.my_lon)/2),2))))::text,''), CASE WHEN b.id IS NOT NULL THEN 'yes' ELSE 'no' END, COALESCE(a.my_locator,''), COALESCE(b.my_locator,'') FROM contest_logs a LEFT JOIN LATERAL (SELECT * FROM contest_logs b WHERE b.my_call=a.dx_call AND b.dx_call=a.my_call AND b.band=a.band AND b.my_lat IS NOT NULL AND abs(extract(epoch FROM (b.worked_at-a.worked_at)))<=900 ORDER BY abs(extract(epoch FROM (b.worked_at-a.worked_at))) LIMIT 1) b ON true ORDER BY a.worked_at DESC;")
            items = [dict(zip(["my_call","dx_call","band","worked_at","km","pair","my_loc","dx_loc"], line.split("|"))) for line in rows.splitlines() if line.strip()]
            return open_json(self, items)
        if self.path.startswith("/api/calls"):
            rows = psql("SELECT callsign, COALESCE(name,''), COALESCE(locator,'') FROM operators ORDER BY callsign;")
            return open_json(self, [dict(zip(["callsign","name","locator"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        if not self.cookie_ok() and self.path.startswith("/api/"): return self.send(401, '{"error":"auth"}', "application/json")
        if self.path.startswith("/api/locators"):
            rows = psql("SELECT code, title FROM locators ORDER BY code;")
            return self.send(200, json.dumps([dict(zip(["code","title"], line.split("|"))) for line in rows.splitlines() if line.strip()], ensure_ascii=False), "application/json")
        if self.path.startswith("/api/qrz"):
            call = parse_qs(self.path.split("?", 1)[-1]).get("call", [""])[0].strip().upper()
            try: found = qrz_lookup(call)
            except Exception as exc: return self.send(404, json.dumps({"error": str(exc)}, ensure_ascii=False), "application/json")
            return self.send(200, json.dumps(found, ensure_ascii=False), "application/json")
        if self.path.startswith("/api/operators"):
            rows = psql("SELECT id, callsign, COALESCE(surname,''), name, COALESCE(patronymic,''), COALESCE(city,''), COALESCE(locator,''), COALESCE(phone,'') FROM operators ORDER BY callsign;")
            return self.send(200, json.dumps([dict(zip(["id","callsign","surname","name","patronymic","city","locator","phone"], line.split("|"))) for line in rows.splitlines() if line.strip()], ensure_ascii=False), "application/json")
        self.send(200, PAGE)
    def read_form(self):
        length = int(self.headers.get("Content-Length", "0"))
        return {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode()).items()}
    def do_POST(self):
        form = self.read_form()
        if self.path.startswith("/api/qso"):
            my_call = form.get("my_call", "").strip().upper()
            dx_call = form.get("dx_call", "").strip().upper()
            when = form.get("when", "").replace("T", " ")
            band = form.get("band", "")
            lat = float(form.get("lat", "0") or 0)
            lon = float(form.get("lon", "0") or 0)
            locator = form.get("locator", "").strip()
            if not my_call or not dx_call or not when or not lat: return open_json(self, {"error": "не заполнено"}, 400)
            psql(f"INSERT INTO contest_logs (my_call, dx_call, band, km, points, worked_at, my_lat, my_lon, my_locator) VALUES ({q(my_call)}, {q(dx_call)}, {q(band)}, 0, 0, {q(when)}, {lat}, {lon}, {q(locator)}) ON CONFLICT DO NOTHING;")
            return open_json(self, {"ok": True})
        if self.path == "/login":
            if not PASSWORD or not same_password(form.get("password", "")): return self.send(403, "no")
            return self.send(200, "ok", extra=f"admin={token()}; HttpOnly; Path=/")
        if not self.cookie_ok(): return self.send(401, "auth")
        callsign = form.get("callsign", "").strip().upper(); name = form.get("name", "").strip()
        if not callsign or not name: return self.send(400, "нужны позывной и имя")
        fields = dict(callsign=callsign, surname=form.get("surname",""), name=name, patronymic=form.get("patronymic",""), city=form.get("city",""), locator=form.get("locator","").upper(), phone=form.get("phone",""))
        if form.get("id"):
            psql(f"UPDATE operators SET callsign={q(fields['callsign'])}, surname={q(fields['surname'])}, name={q(fields['name'])}, patronymic={q(fields['patronymic'])}, city={q(fields['city'])}, locator={q(fields['locator'])}, phone={q(fields['phone'])} WHERE id={int(form['id'])};")
        else:
            psql(f"INSERT INTO operators (callsign, surname, name, patronymic, city, locator, phone) VALUES ({q(fields['callsign'])}, {q(fields['surname'])}, {q(fields['name'])}, {q(fields['patronymic'])}, {q(fields['city'])}, {q(fields['locator'])}, {q(fields['phone'])});")
        self.send(200, '{"ok":true}', "application/json")
    def do_DELETE(self):
        if not self.cookie_ok(): return self.send(401, "auth")
        qs = parse_qs(self.path.split("?", 1)[-1])
        psql(f"DELETE FROM operators WHERE id={int(qs['id'][0])};")
        self.send(200, '{"ok":true}', "application/json")
    def log_message(self, fmt, *args): return
if __name__ == "__main__":
    if not PASSWORD: raise SystemExit("Задайте пароль")
    print(f"Админка: http://{HOST}:{PORT}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
