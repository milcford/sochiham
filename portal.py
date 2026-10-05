        if path == "/api/logs":
            if not self.cookie_ok(): return self.send_json({"error": "auth"}, 401)
            rows = psql("SELECT id, my_call, dx_call, band, to_char(worked_at, 'DD.MM HH24:MI') FROM contest_logs ORDER BY worked_at DESC;")
            return self.send_json([dict(zip(["id","my_call","dx_call","band","worked_at"], line.split("|"))) for line in rows.splitlines() if line.strip()])
        if path.startswith("/api/qso"):
