"""signer.py — ký a_bogus cho submit KHÔNG-CHROME (Giai đoạn 1 engine hybrid C+A).

Cốt lõi bước GỬI của Dola chỉ cần a_bogus (chữ ký request). Poll + tải đã HTTP thuần rồi. Module này gom
việc ký lại một chỗ, hai backend:

  - 'web'   : a_bogus_web.long_a_bogus — thuần Python, KHÔNG Chrome (nhanh, nhẹ; đối thủ ManixAITools cũng
              hướng này). Rủi ro duy nhất: ByteDance đổi thuật toán ký mỗi quý → chữ ký lệch bản.
  - 'chrome': DỰ PHÒNG — 1 Chrome THƯỜNG TRÚ tính a_bogus (cắm ở Giai đoạn 2 qua set_chrome_signer()).
              Luôn đúng vì chạy JS Dola thật, nhưng cần 1 Chrome dùng chung (không phải mỗi nick 1 Chrome).

config qua env DOLA_SIGNER_BACKEND: 'web' (mặc định) | 'chrome' | 'auto'.
  'auto' = thử web trước; web lỗi/chưa cấu hình → rơi về chrome nếu đã cắm.

Giai đoạn 1 KHÔNG đụng đường Chrome/nick hiện tại — chỉ là thư viện đứng riêng, có self-check.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

DEFAULT_AID = 497858        # aid cổng web Dola (khớp test_web_submit.py)
DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")


class SignerError(RuntimeError):
    """Ký a_bogus thất bại ở backend đang chọn (và không có dự phòng khả dụng)."""


# Backend dự phòng (Chrome thường trú) cắm ở Giai đoạn 2. Ký kiểu (query_string, body_json, aid, user_agent) -> a_bogus.
_chrome_signer: Optional[Callable[..., str]] = None
_last_backend: str = ""


def set_chrome_signer(fn: Optional[Callable[..., str]]) -> None:
    """Giai đoạn 2 gọi để cắm backend Chrome thường trú (tránh import vòng: signer.py không biết browser)."""
    global _chrome_signer
    _chrome_signer = fn


def backend_mode() -> str:
    return (os.getenv("DOLA_SIGNER_BACKEND", "web") or "web").strip().lower()


def last_backend() -> str:
    """Backend đã ký lần gần nhất ('web'|'chrome') — để UI/log biết đang chạy đường nào."""
    return _last_backend


def _sign_web(query_string: str, body_json: str, aid: int, user_agent: str) -> str:
    from a_bogus_web import long_a_bogus   # import trễ: chỉ nạp khi thật sự ký web
    ab = long_a_bogus(query_string, body_json, {"aid": int(aid), "userAgent": user_agent})
    if not ab or len(ab) < 100:            # a_bogus hợp lệ ~176 ký tự; ngắn bất thường = lỗi thuật toán
        raise SignerError(f"a_bogus web bất thường (len={len(ab or '')})")
    return ab


def sign(query_string: str, body_json: str, *,
         aid: int = DEFAULT_AID, user_agent: str = DEFAULT_UA) -> str:
    """Trả a_bogus cho (query_string, body_json). Chọn backend theo DOLA_SIGNER_BACKEND.

    'web'    → chỉ Python (lỗi thì raise SignerError, KHÔNG mở Chrome).
    'chrome' → chỉ dùng backend Chrome đã cắm (chưa cắm → SignerError).
    'auto'   → web trước; web lỗi + có Chrome signer → rơi về chrome.
    """
    global _last_backend
    mode = backend_mode()

    if mode == "chrome":
        if not _chrome_signer:
            raise SignerError("DOLA_SIGNER_BACKEND=chrome nhưng chưa cắm Chrome signer (Giai đoạn 2)")
        ab = _chrome_signer(query_string, body_json, aid=aid, user_agent=user_agent)
        _last_backend = "chrome"
        return ab

    # 'web' hoặc 'auto': thử web trước
    try:
        ab = _sign_web(query_string, body_json, aid, user_agent)
        _last_backend = "web"
        return ab
    except Exception as e:  # noqa: BLE001 — lỗi web thì cân nhắc rơi về chrome
        if mode == "auto" and _chrome_signer:
            print(f"[signer] web lỗi ({e}) → rơi về Chrome thường trú", flush=True)
            ab = _chrome_signer(query_string, body_json, aid=aid, user_agent=user_agent)
            _last_backend = "chrome"
            return ab
        raise SignerError(f"ký a_bogus (web) thất bại: {e}") from e


def _demo() -> None:
    """Self-check: backend web ký ra a_bogus hợp lệ; 'auto' rơi về chrome khi web hỏng."""
    os.environ["DOLA_SIGNER_BACKEND"] = "web"
    ab = sign("device_platform=web_pc&aid=497858", '{"x":1}')
    assert isinstance(ab, str) and len(ab) >= 100, ab
    assert last_backend() == "web"

    # auto + web hỏng (ép long_a_bogus lỗi) → phải rơi về chrome đã cắm
    os.environ["DOLA_SIGNER_BACKEND"] = "auto"
    set_chrome_signer(lambda q, b, **kw: "CHROME_FALLBACK_" + "A" * 120)
    import a_bogus_web
    orig = a_bogus_web.long_a_bogus
    try:
        a_bogus_web.long_a_bogus = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("giả lệch bản"))
        ab2 = sign("q=1", "{}")
        assert ab2.startswith("CHROME_FALLBACK_") and last_backend() == "chrome", ab2
    finally:
        a_bogus_web.long_a_bogus = orig
        set_chrome_signer(None)

    # chrome-only nhưng chưa cắm → SignerError
    os.environ["DOLA_SIGNER_BACKEND"] = "chrome"
    try:
        sign("q=1", "{}")
        raise AssertionError("phải raise khi chrome chưa cắm")
    except SignerError:
        pass
    os.environ["DOLA_SIGNER_BACKEND"] = "web"
    print("signer OK: web ký ok, auto rơi về chrome ok, chrome-chưa-cắm raise ok. a_bogus mẫu:", ab[:32], "…")


if __name__ == "__main__":
    _demo()
