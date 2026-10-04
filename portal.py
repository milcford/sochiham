        if path == "/api/round/login":
            callsign = form.get("callsign", "").strip().upper()
            row = psql("SELECT COALESCE(callsign,''), COALESCE(password,'') FROM round_host WHERE id=1;").strip().split("|")
            if not row or row[0] != callsign or not row[1] or not hmac.compare_digest(form.get("password", "").encode(), row[1].encode()):
                return self.send_json({"error": "no"}, 403)
            return self.send_html("ok", extra=f"host={host_token()}; HttpOnly; Path=/")
