"""a_bogus web signer — port từ 5201213/doubao-free-api pure_signer.py (bỏ phần dựng URL doubao.com không dùng).
Tính a_bogus cho cổng web ByteDance/Dola (www.dola.com/chat/completion) bằng Python thuần, không cần Chrome.
CẢNH BÁO: thuật toán ByteDance xoay ~mỗi quý (salt/alphabet/layout). Nguồn push 2026-06-09 → phải kiểm bằng
gửi thật (submit_http.py --send); Dola bắt CAPTCHA/param-error ⇒ đã lệch bản. Gửi thật OK ngày 2026-09-19.
"""
import hashlib
import json
import random
import secrets
import time
from typing import Any, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
DEFAULT_AID = 495671   # aid cổng web Dola (khớp signer.DEFAULT_AID; 497858 cũ của doubao là SAI)

MSTOKEN_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
DEFAULT_ORIGIN = "https://www.doubao.com"
B64_ALPHABET = "Dkdpgh4ZKsQB80/Mfvw36XI1R25-WUAlEi7NLboqYTOPuzmFjJnryx9HVGcaStCe="
S3_ALPHABET = "ckdp1h4ZKsUB80/Mfvw36XIgR25+WQAlEi7NLboqYTOPuzmFjJnryx9HVGDaStCe"
S4_ALPHABET = "Dkdpgh2ZmsQB80/MfvV36XI1R45-WUAlEixNLwoqYTOPuzKFjJnry79HbGcaStCe"
_bogus_index = 0


def fake_mstoken(length: int = 172) -> str:
    if length <= 1:
        return ""
    return "".join(secrets.choice(MSTOKEN_ALPHABET) for _ in range(length - 1)) + "="


# ---- Python-native a_bogus signer. ----

def _u32(x: int) -> int:
    return x & 0xFFFFFFFF


def _rotl(x: int, n: int) -> int:
    n %= 32
    return _u32((x << n) | (x >> (32 - n)))


def _sm3_p0(x: int) -> int:
    return x ^ _rotl(x, 9) ^ _rotl(x, 17)


def _sm3_p1(x: int) -> int:
    return x ^ _rotl(x, 15) ^ _rotl(x, 23)


def _to_bytes_for_hash(x: Any) -> bytes:
    if x is None:
        return b""
    if isinstance(x, bytes):
        return x
    if isinstance(x, bytearray):
        return bytes(x)
    if isinstance(x, (list, tuple)):
        return bytes(int(v) & 255 for v in x)
    return str(x).encode("utf-8")


def sm3_digest(data: Any) -> bytes:
    msg = bytearray(_to_bytes_for_hash(data))
    bit_len = len(msg) * 8
    msg.append(0x80)
    while len(msg) % 64 != 56:
        msg.append(0)
    msg.extend(bit_len.to_bytes(8, "big"))

    v = [
        0x7380166F, 0x4914B2B9, 0x172442D7, 0xDA8A0600,
        0xA96F30BC, 0x163138AA, 0xE38DEE4D, 0xB0FB0E4E,
    ]
    for off in range(0, len(msg), 64):
        block = msg[off:off + 64]
        w = [int.from_bytes(block[i:i + 4], "big") for i in range(0, 64, 4)]
        for j in range(16, 68):
            w.append(_u32(_sm3_p1(w[j - 16] ^ w[j - 9] ^ _rotl(w[j - 3], 15)) ^ _rotl(w[j - 13], 7) ^ w[j - 6]))
        w1 = [_u32(w[j] ^ w[j + 4]) for j in range(64)]
        a, b, c, d, e, f, g, h = v
        for j in range(64):
            tj = 0x79CC4519 if j <= 15 else 0x7A879D8A
            ss1 = _rotl(_u32(_rotl(a, 12) + e + _rotl(tj, j)), 7)
            ss2 = ss1 ^ _rotl(a, 12)
            if j <= 15:
                ff = a ^ b ^ c
                gg = e ^ f ^ g
            else:
                ff = (a & b) | (a & c) | (b & c)
                gg = (e & f) | ((~e) & g)
            tt1 = _u32(ff + d + ss2 + w1[j])
            tt2 = _u32(gg + h + ss1 + w[j])
            d = c
            c = _rotl(b, 9)
            b = a
            a = tt1
            h = g
            g = _rotl(f, 19)
            f = e
            e = _sm3_p0(tt2)
        v = [_u32(x ^ y) for x, y in zip(v, [a, b, c, d, e, f, g, h])]
    return b"".join(x.to_bytes(4, "big") for x in v)


def md5_hex(data: Any) -> str:
    if data is None:
        return "f" * 32
    return hashlib.md5(_to_bytes_for_hash(data)).hexdigest()


def sm3_bytes(data: Any) -> list[int]:
    return list(sm3_digest(data))


def sm3_twice(data: Any) -> list[int]:
    return sm3_bytes(bytes(sm3_bytes(data)))


def bytes_of(x: Any) -> list[int]:
    if isinstance(x, (list, tuple)):
        return [int(v) & 255 for v in x]
    if isinstance(x, (bytes, bytearray, memoryview)):
        return [int(v) & 255 for v in bytes(x)]
    s = str("" if x is None else x)
    raw = s.encode("utf-16-be", errors="surrogatepass")
    out = []
    for i in range(0, len(raw), 2):
        hi, lo = raw[i], raw[i + 1]
        if hi:
            out.extend([hi, lo])
        else:
            out.append(lo)
    return out


def hex_to_bytes(hex_str: str) -> list[int]:
    return [int(hex_str[i:i + 2], 16) & 255 for i in range(0, len(hex_str), 2)]


def rc4(key_byte: int, plaintext: list[int]) -> list[int]:
    key = chr(key_byte & 255)
    s = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s[i] + ord(key[i % len(key)])) & 255
        s[i], s[j] = s[j], s[i]
    a = 0
    j = 0
    out = []
    for b in plaintext:
        a = (a + 1) & 255
        j = (j + s[a]) & 255
        s[a], s[j] = s[j], s[a]
        out.append((int(b) ^ s[(s[a] + s[j]) & 255]) & 255)
    return out


def rc4_long(key_bytes: Any, data_bytes: Any) -> list[int]:
    key = bytes_of(key_bytes)
    data = bytes_of(data_bytes)
    s = [255 - i for i in range(256)]
    j = 0
    for i in range(256):
        j = (j + (s[i] * j) + key[i % len(key)]) & 255
        s[i], s[j] = s[j], s[i]
    a = 0
    j = 0
    out = []
    for b in data:
        a = (a + 1) & 255
        j = (j + s[a]) & 255
        s[a], s[j] = s[j], s[a]
        out.append((b ^ s[(s[a] + s[j]) & 255]) & 255)
    return out


def custom_base64(data: list[int], alphabet: str = B64_ALPHABET) -> str:
    out = []
    i = 0
    while len(data) >= i + 3:
        n = ((data[i] & 255) << 16) | ((data[i + 1] & 255) << 8) | (data[i + 2] & 255)
        i += 3
        out.append(alphabet[(0xFC0000 & n) >> 18])
        out.append(alphabet[(0x3F000 & n) >> 12])
        out.append(alphabet[(0xFC0 & n) >> 6])
        out.append(alphabet[0x3F & n])
    if len(data) - i > 0:
        n = ((data[i] & 255) << 16) | (((data[i + 1] & 255) << 8) if len(data) > i + 1 else 0)
        out.append(alphabet[(0xFC0000 & n) >> 18])
        out.append(alphabet[(0x3F000 & n) >> 12])
        out.append(alphabet[(0xFC0 & n) >> 6] if len(data) > i + 1 else "=")
        out.append("=")
    return "".join(out)


def ob4(pair: Any, mode: int = 0) -> list[int]:
    p = bytes_of(pair)
    lo = random.randrange(65535) & 255
    hi = (random.randrange(65535) >> 8) & 255
    if mode == 1:
        hi = random.randrange(40)
    x0 = p[0] if len(p) > 0 else 0
    x1 = p[1] if len(p) > 1 else 0
    return [(lo & 170) | (x0 & 85), (lo & 85) | (x0 & 170), (hi & 170) | (x1 & 85), (hi & 85) | (x1 & 170)]


def ob_chunk3(data: Any) -> list[int]:
    a = bytes_of(data)
    out = []
    i = 0
    while i < len(a):
        if i + 2 < len(a):
            r = random.randrange(1000) & 255
            x0, x1, x2 = a[i], a[i + 1], a[i + 2]
            out.extend([(r & 145) | (x0 & 110), (r & 66) | (x1 & 189), (r & 44) | (x2 & 211), (x0 & 145) | (x1 & 66) | (x2 & 44)])
            i += 3
        else:
            out.append(a[i] & 255)
            i += 1
            if i < len(a) and a[i]:
                out.append(a[i] & 255)
            i += 1
    return out


def env_base64(envcode: int, ubcode: int, ua: str) -> str:
    key = chr((envcode // 256) & 255) + chr(envcode % 256) + chr(ubcode % 256)
    return custom_base64(rc4_long(key, ua), S3_ALPHABET)


def parse_version_bytes(version: str) -> list[int]:
    if version == "1.0.1.20-alpha.14":
        return [1, 0, 0, 0, 129, 4, 170, 84]
    if version == "1.0.1.20-alpha.13":
        return [1, 0, 0, 0, 129, 4, 170, 78]
    parts = [int(x) if x.isdigit() else 0 for x in str(version or "").split(".")]
    while len(parts) < 5:
        parts.append(0)
    return [parts[0] & 255, parts[1] & 255, parts[2] & 255, parts[3] & 255, 129, 4, 170, (parts[4] * 6) & 255]


def _byte_at(n: int, k: int) -> int:
    return int(n // (256 ** k)) & 255


def long_a_bogus(query_string: str, body: str, opts: Optional[dict[str, Any]] = None) -> str:
    opts = opts or {}
    ua = opts.get("userAgent") or USER_AGENT
    page_id = int(opts.get("pageId", 26930))
    aid = int(opts.get("aid", DEFAULT_AID))
    version = opts.get("sdkVersion") or "1.0.1.20-alpha.14"
    now = int(opts.get("now", int(time.time() * 1000)))
    envcode = int(opts.get("envcode", 129))
    ubcode = int(opts.get("ubcode", 14))
    env_obj = opts.get("envObj") or [0, 0, 0, 0, 79]
    salt = "dhzx"

    qh = sm3_twice(str(query_string or "") + salt)
    bh = sm3_twice(str(body or "") + salt)
    env_str = env_base64(envcode, ubcode, ua)
    eh = sm3_bytes(env_str)
    ink = int(opts.get("ink", now - 1))
    day = int((int(time.time() * 1000) - 1721836800000) / 1000 / 60 / 60 / 24 / 14)
    perf = int(opts.get("perf", 6))
    delta = int(opts.get("delta", 3))
    t = now

    time_bytes = [_byte_at(t, i) for i in range(6)]
    ink_bytes = [_byte_at(ink, i) for i in range(6)]
    aid_bytes = [_byte_at(aid, i) for i in range(4)]
    page_bytes = [_byte_at(page_id, i) for i in range(4)]
    env_code_bytes = [envcode & 255, (envcode >> 8) & 255]
    env4 = int(env_obj[4] or 0)
    env_bytes = [env4 & 255, (env4 >> 8) & 255, int(env_obj[0] or 0), int(env_obj[1] or 0), int(env_obj[2] or 0), int(env_obj[3] or 0)]
    ub_bytes = [ubcode & 255, (ubcode >> 8) & 255, (ubcode >> 16) & 255, (ubcode >> 24) & 255]

    h51 = qh[3]
    while h51 == 11:
        h51 = 12
    if env4 & 2:
        h51 = 11
    h55 = bh[4]
    while h55 == 8:
        h55 = 9
    if env4 & 4:
        h55 = 8
    h59 = eh[5]
    while h59 == 12:
        h59 = 13
    if env4 & 8:
        h59 = 12

    screen_str = f"{opts.get('innerWidth', 0)}|{opts.get('innerHeight', 0)}|{opts.get('outerWidth', 0)}|{opts.get('outerHeight', 0)}|{opts.get('availWidth', 1440)}|{opts.get('availHeight', 900)}|{opts.get('sizeWidth', 1440)}|{opts.get('sizeHeight', 900)}|{opts.get('platform', 'MacIntel')}"
    screen_bytes = bytes_of(screen_str)
    screen_len = len(screen_bytes)
    comma_str = f"{(t + 3) & 255},"
    comma_bytes = bytes_of(comma_str)
    comma_len = len(comma_bytes)
    vbytes = parse_version_bytes(version)

    xorv = 0
    for b in vbytes:
        xorv ^= b
    for b in [41, day, perf, delta, *time_bytes, *env_code_bytes, 3, *env_bytes, *ub_bytes, qh[9], qh[18], h51, bh[10], bh[19], h55, eh[11], eh[21], h59, *ink_bytes, *page_bytes, *aid_bytes, screen_len & 255, (screen_len >> 8) & 255, comma_len & 255, (comma_len >> 8) & 255]:
        xorv ^= int(b) & 255

    base50 = [
        time_bytes[5], ub_bytes[0], eh[11], ink_bytes[1], aid_bytes[2], time_bytes[0], page_bytes[3], ub_bytes[1],
        env_code_bytes[0], qh[18], env_bytes[0], 3, h51, page_bytes[1], delta, qh[9],
        ink_bytes[4], ub_bytes[3], time_bytes[1], aid_bytes[0], day, h55, time_bytes[2], page_bytes[2],
        h59, env_bytes[2], ink_bytes[2], ink_bytes[3], perf, aid_bytes[1], env_bytes[3], aid_bytes[3],
        eh[21], bh[10], env_bytes[4], env_bytes[1], time_bytes[4], page_bytes[0], bh[19], env_bytes[5],
        ink_bytes[5], ub_bytes[2], env_code_bytes[1], 41, ink_bytes[0], time_bytes[3], screen_len & 255, screen_len >> 8, comma_len & 255, comma_len >> 8,
    ]
    packet = [x & 255 for x in base50] + screen_bytes + comma_bytes + [xorv & 255]
    prefix = ob4([3, 82], 1)
    mid = ob_chunk3(packet)
    encrypted = rc4_long(chr(211), bytes_of(vbytes) + mid)
    return custom_base64(prefix + encrypted, S4_ALPHABET)


def _canonical_url_and_query(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    if not parts.scheme:
        parts = urlsplit(DEFAULT_ORIGIN.rstrip("/") + "/" + url.lstrip("/"))
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "a_bogus"]
    query = urlencode(pairs)
    clean_url = urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    return clean_url, query


def sign_payload_python(payload: dict[str, Any]) -> dict[str, Any]:
    url = payload.get("url") or "/chat/completion"
    clean_url, query = _canonical_url_and_query(url)
    body = payload.get("body", {})
    body_json = body if isinstance(body, str) else json.dumps(body or {}, ensure_ascii=False, separators=(",", ":"))
    a_bogus = long_a_bogus(query, body_json, payload.get("signOptions") or {})
    sep = "&" if urlsplit(clean_url).query else "?"
    signed_url = f"{clean_url}{sep}{urlencode({'a_bogus': a_bogus})}"
    return {
        "ok": True,
        "mode": "python_pure_hand_long_abogus",
        "signed_url": signed_url,
        "a_bogus": a_bogus,
        "headers": dict(payload.get("headers") or {}),
        "payload": {**payload, "body": body_json},
    }


def _self_check() -> None:
    """Kiểm nội bộ: cùng input (đóng băng now + seed random) → a_bogus tất định, đúng khuôn base64/S4."""
    import random as _r
    opts = {"now": 1_726_000_000_000, "aid": 495671, "innerWidth": 1440, "innerHeight": 900}
    _r.seed(42); a = long_a_bogus("aid=495671&x=1", '{"m":"hi"}', dict(opts))
    _r.seed(42); b = long_a_bogus("aid=495671&x=1", '{"m":"hi"}', dict(opts))
    assert a == b, "a_bogus không tất định khi đóng băng now+seed"
    assert 80 <= len(a) <= 260 and all(c in S4_ALPHABET + "=" for c in a), f"khuôn a_bogus lạ: len={len(a)}"
    _r.seed(42); c = long_a_bogus("aid=495671&x=2", '{"m":"hi"}', dict(opts))
    assert c != a, "a_bogus không đổi theo query"
    su = sign_payload_python({"url": "https://www.dola.com/chat/completion?aid=495671", "body": {"m": "hi"},
                              "signOptions": {"aid": 495671}})
    assert "a_bogus=" in su["signed_url"] and su["a_bogus"]
    print("a_bogus_web self-check OK · a_bogus mẫu:", a[:40], "… (", len(a), "ký tự )")


if __name__ == "__main__":
    _self_check()
