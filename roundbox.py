def host_token():
    return hmac.new(SECRET.encode(), b"host-ok", hashlib.sha256).hexdigest()
def ensure_round():
    psql("CREATE TABLE IF NOT EXISTS round_host (id int PRIMARY KEY, callsign text DEFAULT '', password text DEFAULT '', current_call text DEFAULT '', current_name text DEFAULT '');")
    psql("INSERT INTO round_host (id) VALUES (1) ON CONFLICT DO NOTHING;")
    psql("CREATE TABLE IF NOT EXISTS round_queue (id serial PRIMARY KEY, pos int DEFAULT 0, callsign text, name text);")
def round_view():
    host = psql("SELECT COALESCE(callsign,''), COALESCE(current_call,''), COALESCE(current_name,'') FROM round_host WHERE id=1;").strip().split("|")
    rows = psql("SELECT id, callsign, COALESCE(name,'') FROM round_queue ORDER BY pos, id;")
    queue = [dict(zip(["id","callsign","name"], line.split("|"))) for line in rows.splitlines() if line.strip()]
    return {"host": host[0] if host and host[0] else "", "current_call": host[1] if len(host)>1 else "", "current_name": host[2] if len(host)>2 else "", "queue": queue}
