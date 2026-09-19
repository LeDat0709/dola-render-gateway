"""dola_video_decoder.py — Giải mã token qAAB của Dola / Doubao để lấy link video MP4 1080P Master gốc không watermark.

Thuật toán trích xuất trực tiếp từ Seedance Studio Pro v1.1 (background.js:774-1020).
Thay vì tải video có logo rồi dùng ffmpeg/delogo để xóa watermark, module này cho phép
lấy trực tiếp URL sạch 100% từ cụm CDN của ByteDance.

Quy trình:
1. Nhận fallback_api URL từ Dola response/poll.
2. Thay thế params: channel="no", codec_type="8", logo_type="unwatermarked".
3. Gọi GET fallback_api để nhận JSON metadata.
4. Trích xuất main_url token (qAAB...) và key_seed.
5. Hash SHA-512 2 lớp với salt bí mật để tạo AES-128 Key + IV.
6. Decrypt AES-128-CBC -> Direct MP4 URL sạch không logo.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    _HAS_CRYPTO = True
except ImportError:
    _HAS_CRYPTO = False

# Salt tĩnh 128-byte được ByteDance nhúng trong web app & extension
QAAB_SALT_HEX = (
    "4dd4c2e6b83162090e52b3c7a6733ba4"
    "1cb2462b829ab58a196b39db57177524"
    "f49baf7f08e8d68d26a72e37c1a95a2f"
    "1f05a51892aef2949732b62a38aadd58"
)
QAAB_SALT = bytes.fromhex(QAAB_SALT_HEX)


def is_http_url(s: str) -> bool:
    return bool(s and (s.startswith("http://") or s.startswith("https://")))


def base64_decode_loose(text: str) -> bytes | None:
    """Giải mã base64 linh hoạt hỗ trợ urlsafe và các ký tự thay thế."""
    if not text:
        return None
    cleaned = text.strip()
    variants = [
        cleaned,
        cleaned.translate(str.maketrans("$@#", "_/.")),
        cleaned.translate(str.maketrans("$@#", "+/=")),
    ]
    seen = set()
    for cand in variants:
        if not cand or cand in seen:
            continue
        seen.add(cand)
        # Pad to multiple of 4
        pad = (4 - (len(cand) % 4)) % 4
        padded = cand + ("=" * pad)
        # Chuẩn hoá - _ thành + /
        normalized = padded.replace("-", "+").replace("_", "/")
        try:
            return base64.b64decode(normalized)
        except Exception:
            continue
    return None


def strip_pkcs7(data: bytes) -> bytes:
    """Loại bỏ PKCS#7 padding sau khi giải mã AES."""
    if not data:
        return b""
    pad = data[-1]
    if pad < 1 or pad > 16 or pad > len(data):
        return data
    if all(b == pad for b in data[-pad:]):
        return data[:-pad]
    return data


def decrypt_aes_cbc_url(payload: bytes, key: bytes, iv: bytes) -> str:
    """Giải mã AES-128-CBC và chuyển thành HTTP URL."""
    if not _HAS_CRYPTO or not payload or len(payload) % 16 != 0:
        return ""
    try:
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()
        plain = decryptor.update(payload) + decryptor.finalize()

        # Thử parse trực tiếp
        try:
            url = plain.decode("latin1", errors="ignore").strip()
            if is_http_url(url):
                return url
        except Exception:
            pass

        # Thử strip PKCS#7
        stripped = strip_pkcs7(plain)
        url = stripped.decode("latin1", errors="ignore").strip()
        return url if is_http_url(url) else ""
    except Exception:
        return ""


def decode_qaab_token(token: str, key_seed: str) -> str:
    """Giải mã token qAAB bằng key_seed và QAAB_SALT_HEX (Seedance standard)."""
    data = base64_decode_loose(token)
    seed = base64_decode_loose(key_seed)
    if not data or not seed:
        return ""

    # 1. digest1 = SHA-512(seed[:32])
    digest1 = hashlib.sha512(seed[:32]).digest()

    # 2. digest2 = SHA-512(digest1 + salt)
    digest2 = hashlib.sha512(digest1 + QAAB_SALT).digest()

    key = digest2[:16]  # 16 bytes = 128 bit AES key
    iv = digest2[16:32]  # 16 bytes IV

    attempts: list[tuple[bytes, bytes, bytes]] = []

    # Kiểm tra header ByteDance (0xa8, 0x00, 0x01, 0x00)
    if len(data) >= 4 and data[0] == 0xA8 and data[1] == 0x00 and data[2] == 0x01 and data[3] == 0x00:
        attempts.append((data[4:], key, iv))
        attempts.append((data[4:], iv, key))
        if len(data) > 36:
            attempts.append((data[36:], key, data[20:36]))
            attempts.append((data[36:], key, iv))
    else:
        attempts.append((data, key, iv))

    for payload, k, v in attempts:
        url = decrypt_aes_cbc_url(payload, k, v)
        if url:
            return url

    return ""


def decode_main_url(token: str, key_seed: str = "") -> str:
    """Giải mã token thành URL trực tiếp (hỗ trợ plain url, base64 url, và qAAB token)."""
    if is_http_url(token):
        return token

    # Thử decode plain base64
    raw_bytes = base64_decode_loose(token)
    if raw_bytes:
        try:
            cand = raw_bytes.decode("utf-8", errors="ignore").strip()
            if is_http_url(cand):
                return cand
        except Exception:
            pass

    # Giải mã qAAB token
    if token.startswith("qAAB") and key_seed:
        return decode_qaab_token(token, key_seed)

    return ""


def extract_key_seed(value: Any, depth: int = 0) -> str:
    """Đào sâu cấu trúc dict/list/string để tìm key_seed."""
    if depth > 10 or value is None:
        return ""
    if isinstance(value, str):
        m = re.search(r'(?:^|[?&])key_seed=([^&"\'<>\\\s]+)', value, re.IGNORECASE)
        if m:
            return m.group(1)
        m2 = re.search(r'["\']key_seed["\']\s*:\s*["\']([^"\']+)', value, re.IGNORECASE)
        if m2:
            return m2.group(1)
        return ""
    if isinstance(value, dict):
        if "key_seed" in value and isinstance(value["key_seed"], str):
            return value["key_seed"]
        for v in value.values():
            hit = extract_key_seed(v, depth + 1)
            if hit:
                return hit
    elif isinstance(value, list):
        for item in value:
            hit = extract_key_seed(item, depth + 1)
            if hit:
                return hit
    return ""


def prepare_fallback_api_url(fallback_api: str) -> str:
    """Thêm tham số channel=no, codec_type=8, logo_type=unwatermarked vào URL."""
    parsed = urlparse(fallback_api)
    query = parse_qs(parsed.query)
    query["channel"] = ["no"]
    query["codec_type"] = ["8"]
    query["logo_type"] = ["unwatermarked"]
    new_query = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


async def resolve_unwatermarked_video_url(fallback_api: str, proxy: str | None = None) -> str:
    """Gọi fallback_api để resolve link video 1080P sạch không watermark."""
    import aiohttp

    target_url = prepare_fallback_api_url(fallback_api)
    timeout = aiohttp.ClientTimeout(total=20)

    async with aiohttp.ClientSession(timeout=timeout) as session:
        headers = {"accept": "application/json,text/plain,*/*"}
        async with session.get(target_url, headers=headers, proxy=proxy) as resp:
            if resp.status != 200:
                return ""
            try:
                payload = await resp.json(content_type=None)
            except Exception:
                return ""
    if not isinstance(payload, dict):   # mảng lỗi / chuỗi / số: không có video → "" như hợp đồng
        return ""

    key_seed = extract_key_seed(payload) or extract_key_seed(fallback_api)

    # Lấy video_info
    nested = payload.get("data")
    nested = nested if isinstance(nested, dict) else {}
    video_info = payload.get("video_info") or nested.get("video_info") or payload
    data = (video_info.get("data") if isinstance(video_info, dict) else None) or video_info

    # Tìm best token trong video_list
    video_list = data.get("video_list") if isinstance(data, dict) else None
    entries = list(video_list.values()) if isinstance(video_list, dict) else ([data] if isinstance(data, dict) else [])

    best_token = ""
    best_score = -1

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        token = (entry.get("main_url") or entry.get("play_url") or "").strip()
        if not token:
            continue
        score = (
            float(entry.get("bitrate") or entry.get("real_bitrate") or 0)
            + float(entry.get("vwidth") or entry.get("width") or 0)
            * float(entry.get("vheight") or entry.get("height") or 0)
        )
        if score > best_score:
            best_score = score
            best_token = token

    if not best_token:
        return ""

    return decode_main_url(best_token, key_seed)
