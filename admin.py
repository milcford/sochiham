    grid = tag_text(data, "locator") or tag_text(data, "grid")
    phone = tag_text(data, "phone") or tag_text(data, "tel") or tag_text(data, "telephone")
    return {"callsign": tag_text(data, "call") or call, "surname": tag_text(data, "surname"), "name": tag_text(data, "name"), "patronymic": tag_text(data, "name2"), "city": tag_text(data, "city").rstrip(","), "locator": grid[:4].upper(), "phone": phone}
