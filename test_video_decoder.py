"""Test suite for Dola video qAAB decoder and ProseMirror prompt injection."""
import hashlib
import pytest
from dola_video_decoder import (
    QAAB_SALT,
    decode_main_url,
    decode_qaab_token,
    prepare_fallback_api_url,
    extract_key_seed,
)


def test_prepare_fallback_api_url():
    url = "https://example.com/api/video?vid=123&foo=bar"
    prepared = prepare_fallback_api_url(url)
    assert "channel=no" in prepared
    assert "codec_type=8" in prepared
    assert "logo_type=unwatermarked" in prepared


def test_extract_key_seed():
    # From query string
    url = "https://example.com/api?key_seed=my_seed_123"
    assert extract_key_seed(url) == "my_seed_123"

    # From JSON object
    data = {"video_info": {"key_seed": "seed_inside_nested"}}
    assert extract_key_seed(data) == "seed_inside_nested"


def test_decode_qaab_token_roundtrip():
    # cryptography không nằm trong requirements.txt: import ở đầu file làm HỎNG CẢ FILE test khi máy thiếu nó
    ciphers = pytest.importorskip("cryptography.hazmat.primitives.ciphers")
    Cipher, algorithms, modes = ciphers.Cipher, ciphers.algorithms, ciphers.modes
    # Giả lập 1 token hợp lệ được mã hóa bằng AES-CBC
    seed_str = "test_key_seed_value_for_dola_123456789"
    import base64
    seed_b64 = base64.b64encode(seed_str.encode()).decode()

    # Tạo Key/IV giống decode_qaab_token
    digest1 = hashlib.sha512(seed_str.encode()[:32]).digest()
    digest2 = hashlib.sha512(digest1 + QAAB_SALT).digest()
    key = digest2[:16]
    iv = digest2[16:32]

    # Target URL
    target_url = "https://v16-dola.ibytedtos.com/media/master_1080p_clean.mp4"
    plain_bytes = target_url.encode()
    # PKCS#7 pad to 16
    pad = 16 - (len(plain_bytes) % 16)
    padded = plain_bytes + bytes([pad] * pad)

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()
    ciphertext = encryptor.update(padded) + encryptor.finalize()

    # Header 0xa8 0x00 0x01 0x00
    token_bytes = bytes([0xA8, 0x00, 0x01, 0x00]) + ciphertext
    token_b64 = "qAAB" + base64.b64encode(token_bytes).decode()

    # Test decode
    decoded = decode_main_url(token_b64[4:], seed_b64)
    assert decoded == target_url


class _FakeResp:
    status = 200

    def __init__(self, payload):
        self._payload = payload

    async def json(self, content_type=None):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeSession(_FakeResp):
    def get(self, *args, **kwargs):
        return _FakeResp(self._payload)


@pytest.mark.parametrize("payload", [[], "oops", 3, {"data": []}, {"data": "x"}])
async def test_resolve_non_object_json_returns_empty(monkeypatch, payload):
    """fallback_api trả JSON không phải object (mảng lỗi, chuỗi...) → "" như hợp đồng, không AttributeError."""
    import aiohttp
    from dola_video_decoder import resolve_unwatermarked_video_url
    monkeypatch.setattr(aiohttp, "ClientSession", lambda *a, **k: _FakeSession(payload))
    assert await resolve_unwatermarked_video_url("https://x.example/api?vid=1") == ""
