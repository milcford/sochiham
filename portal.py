def qrz_call(path, params):
    url = "https://api.qrz.ru/" + path + "?" + urlencode(params)
    req = Request(url, headers={"User-Agent": "sochiham/1.0"})
    try:
        with urlopen(req, timeout=20) as resp: raw = resp.read()
    except HTTPError as exc:
        raw = exc.read()
    return ET.fromstring(raw)
