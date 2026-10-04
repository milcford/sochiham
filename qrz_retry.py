def qrz_login():
    global QRZ_SESSION
    login = qrz_xml("https://api.qrz.ru/login?" + urlencode({"u": QRZ_USER, "p": QRZ_PASSWORD, "agent": "sochiham"}))
    QRZ_SESSION = tag_text(login, "session_id")
    if not QRZ_SESSION: raise RuntimeError(tag_text(login, "error") or "qrz.ru не пустил")
def qrz_lookup(call):
    global QRZ_SESSION
    if not QRZ_USER or not QRZ_PASSWORD: raise RuntimeError("не заданы QRZ_USER и QRZ_PASSWORD")
    if not QRZ_SESSION: qrz_login()
    data = qrz_xml("https://api.qrz.ru/callsign?" + urlencode({"id": QRZ_SESSION, "callsign": call}))
    if tag_text(data, "error"):
        QRZ_SESSION = ""
        qrz_login()
        data = qrz_xml("https://api.qrz.ru/callsign?" + urlencode({"id": QRZ_SESSION, "callsign": call}))
        if tag_text(data, "error"):
            QRZ_SESSION = ""
            raise RuntimeError(tag_text(data, "error"))
    grid = tag_text(data, "locator") or tag_text(data, "grid")
    phone = tag_text(data, "phone") or tag_text(data, "tel") or tag_text(data, "telephone")
    return {"callsign": tag_text(data, "call") or call, "surname": tag_text(data, "surname"), "name": tag_text(data, "name"), "patronymic": tag_text(data, "name2"), "city": tag_text(data, "city").rstrip(","), "locator": grid[:4].upper(), "phone": phone}
