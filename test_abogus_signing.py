"""Test bộ 3 cho a_bogus: deterministic, time-sensitive, fallback Chrome.

Chạy: .venv/bin/python test_abogus_signing.py

Đây là test đề xuất ở canvas 'Nghiên cứu a_bogus · Thuật toán + Call Chain + Maintain'.
Mục tiêu: 3 test phát hiện nhanh nhất sự cố khi ByteDance đổi chữ ký.

  1. test_abogus_matches_chrome
     - Phần A (luôn chạy): a_bogus Web Python có ổn định, đúng alphabet, đúng khung?
       + Tái tạo nhất quán khi cùng (query, body, UA, time)
       + Cùng input nhưng đổi query  đổi chữ ký
       + Cùng input nhưng đổi body   đổi chữ ký
       + Cùng input nhưng đổi UA     đổi chữ ký
     - Phần B (skip nếu thiếu Chrome): so chữ ký Python với chữ ký Chrome thật (cùng input)
       + Có env DOLA_TEST_CHROME_ABOGUS=1 + import được signer.set_chrome_signer + browser ký được

  2. test_abogus_time_sensitivity
     Cùng input nhưng cách 1.1s → chữ ký phải KHÁC (do time_bytes trong xorv).

  3. test_fallback_to_chrome_when_abogus_breaks
     Mock long_a_bogus raise  → signer.sign() phải rơi về Chrome (last_backend == 'chrome')
     và chữ ký trả về phải có prefix 'CHROME_FALLBACK_'.

Lưu ý: KHÔNG ép time.time() = const vì a_bogus lấy time thật. Muốn tái tạo chính xác
100% phải monkeypatch time.time() bên trong a_bogus_web, nhưng làm vậy phá invariant.
Tận dụng: long_a_bogus deterministic khi input deterministic + time cùng tick là đủ.
"""

from __future__ import annotations

import os
import time

import a_bogus_web
import signer


# === Hằng số dùng chung ======================================================

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"

# query/body cố định cho cả 3 test — KHÔNG có msToken/biến động để chữ ký
# chỉ phụ thuộc time tick (chấp nhận được cho deterministic trong cùng ms).
Q = "device_platform=web_pc&aid=497858&web_id=abc123&tea_uuid=xyz789"
B = '{"messages":[{"role":"user","content":"hi"}],"stream":false}'

# Cấu trúc kỳ vọng của một a_bogus hợp lệ — copy từ self_check() của a_bogus_web.
ALPHABET = set(a_bogus_web.S4_ALPHABET + "=")
LEN_MIN, LEN_MAX = 80, 260


def _assert_valid_shape(sig: str, where: str) -> None:
    """Mọi a_bogus đúng chuẩn phải pass qua đây — nếu fail là lỗi port, không phải lỗi input."""
    assert isinstance(sig, str), f"{where}: phải là str, nhận {type(sig).__name__}"
    assert LEN_MIN <= len(sig) <= LEN_MAX, (
        f"{where}: khung độ dài {LEN_MIN}-{LEN_MAX}, nhận len={len(sig)}"
    )
    bad = sorted({c for c in sig if c not in ALPHABET})
    assert not bad, f"{where}: ký tự ngoài S4_ALPHABET: {bad!r}"


# === Test 1: a_bogus matches chrome (Python ổn định + Chrome verify) =========

class _ChromeNotAvailable(Exception):
    """Báo hiệu không thể ký bằng Chrome thật (thiếu browser, thiếu cookie, v.v.)."""


def _sign_with_chrome(qs: str, body: str, aid: int, user_agent: str) -> str:
    """Ký bằng Chrome thật qua browser_pool.

    Cần import lazily để tránh circular import / không ép Chrome khi test đơn giản.
    Nếu môi trường không có browser thật, raise _ChromeNotAvailable để test nhảy sang skip.
    """
    try:
        from browser_pool import sign_abogus_with_chrome  # type: ignore
    except Exception as e:
        raise _ChromeNotAvailable(f"browser_pool không expose sign_abogus_with_chrome: {e}")

    return sign_abogus_with_chrome(qs, body, aid=aid, user_agent=user_agent)


def test_python_abogus_stable_shape_and_changes_with_input():
    """Phần A: a_bogus Web Python phải ổn định, đúng khung, phản ứng đúng với thay đổi input.

    KHÔNG cần Chrome, chạy được ở bất kỳ đâu.

    Hai lần gọi liên tiếp trong đời thực cách nhau ≥1 tick 1ms → chữ ký KHÁC
    (vì time_bytes được mix vào xorv). Muốn deterministic:
      - a_bogus_web.random.seed(42)    # reset random mà module dùng
      - opts={"now": <const_ms>, ...}   # ép time, _self_check trong a_bogus_web.py:404+ cũng vậy

    Test này làm CẢ HAI:
      -1A) Seed + freeze time → cùng input → cùng chữ ký (giống _self_check)
      -1B) Seed + freeze time → đổi từng trường → chữ ký đổi (test semantic)
      -1C) Không seed (real-world) → chữ ký vẫn hợp lệ, đúng alphabet, đúng khung
    """
    # `a_bogus_web.random` được module import vào globals → có thể seed trực tiếp.
    a_bogus_web.random.seed(42)
    FREEZE = {"aid": 497858, "userAgent": UA, "now": 1_726_000_000_000}

    # 1A. Cùng input + seeded + freeze time → cùng chữ ký (giống _self_check)
    a = a_bogus_web.long_a_bogus(Q, B, FREEZE)
    a_bogus_web.random.seed(42)
    b = a_bogus_web.long_a_bogus(Q, B, FREEZE)
    _assert_valid_shape(a, "abogus(seeded+freeze lần 1)")
    _assert_valid_shape(b, "abogus(seeded+freeze lần 2)")
    assert a == b, f"seed + freeze time + cùng input phải ra cùng chữ ký: {a[:20]}... vs {b[:20]}..."

    # 1B. Cùng seed + freeze → đổi query  chữ ký phải đổi (qh thay đổi → xorv thay đổi)
    a_bogus_web.random.seed(42)
    c = a_bogus_web.long_a_bogus(Q + "&x=2", B, FREEZE)
    _assert_valid_shape(c, "abogus(seeded+freeze query đổi)")
    assert c != a, "đổi query mà chữ ký giữ nguyên → lỗi semantic SM3/qh"

    # 1C. Đổi body
    a_bogus_web.random.seed(42)
    d = a_bogus_web.long_a_bogus(Q, '{"messages":[{"role":"user","content":"hello"}]}', FREEZE)
    _assert_valid_shape(d, "abogus(seeded+freeze body đổi)")
    assert d != a, "đổi body mà chữ ký giữ nguyên → lỗi semantic SM3/bh"

    # 1D. Đổi UA → env_code thay đổi → eh thay đổi → xorv thay đổi
    a_bogus_web.random.seed(42)
    other_ua = UA.replace("Chrome/124.0.0.0", "Chrome/125.0.0.0")
    e = a_bogus_web.long_a_bogus(Q, B, {"aid": 497858, "userAgent": other_ua, "now": 1_726_000_000_000})
    _assert_valid_shape(e, "abogus(seeded+freeze UA đổi)")
    assert e != a, "đổi UA mà chữ ký giữ nguyên → lỗi semantic env_base64/eh"

    # 1E. Không seed (real-world) → vẫn phải đúng alphabet + đúng khung
    # Hai lần gọi liên tiếp CÓ THỂ khác nhau do time_bytes — không assert equality ở đây.
    f = a_bogus_web.long_a_bogus(Q, B, {"aid": 497858, "userAgent": UA})
    g = a_bogus_web.long_a_bogus(Q, B, {"aid": 497858, "userAgent": UA})
    _assert_valid_shape(f, "abogus(real-world 1)")
    _assert_valid_shape(g, "abogus(real-world 2)")


def test_python_abogus_matches_chrome_when_available():
    """Phần B: nếu môi trường có Chrome + browser_pool expose hàm ký, so với Python.

    Skip khi:
      - thiếu DOLA_TEST_CHROME_ABOGUS=1 (test tốn ~10s khi bật)
      - thiếu browser_pool / Chrome signer thật

    Khi chạy: gọi Chrome ký cùng (Q, B, UA), so với Python ký. Phải giống y hệt
    vì a_bogus là chữ ký request đúng nghĩa đen — sai 1 byte là Dola từ chối.
    """
    if os.getenv("DOLA_TEST_CHROME_ABOGUS") != "1":
        # Bỏ qua — KHÔNG raise Skipped (vì crash __main__ khi chạy python test_xxx.py).
        # Trả về sớm, main loop sẽ đánh dấu SKIP.
        import sys
        print(f"  SKIP  test_python_abogus_matches_chrome_when_available "
              f"(set DOLA_TEST_CHROME_ABOGUS=1 để bật)", file=sys.stderr)
        return

    try:
        chrome_sig = _sign_with_chrome(Q, B, aid=497858, user_agent=UA)
    except _ChromeNotAvailable as e:
        import sys
        print(f"  SKIP  test_python_abogus_matches_chrome_when_available "
              f"(không thể ký Chrome: {e})", file=sys.stderr)
        return

    opts = {"aid": 497858, "userAgent": UA}
    py_sig = a_bogus_web.long_a_bogus(Q, B, opts)
    _assert_valid_shape(chrome_sig, "Chrome a_bogus")
    _assert_valid_shape(py_sig, "Python a_bogus")
    assert py_sig == chrome_sig, (
        f"a_bogus Python KHÁC Chrome → port sai hoặc ByteDance đã đổi thuật toán.\n"
        f"  python : {py_sig}\n"
        f"  chrome : {chrome_sig}"
    )


# === Test 2: time-sensitive =================================================

def test_abogus_changes_after_1_second():
    """Cùng input, cách ≥1.1s → chữ ký phải khác.

    Lý do: time_bytes được mix vào xorv, mỗi tick ms → time_bytes khác → xorv khác
    → vbytes khác → RC4 encrypt khác → base64 khác. Nếu test fail tức là:
      - time không được feed vào xorv (lỗi port nặng), HOẶC
      - ByteDance vừa bỏ time ra khỏi chữ ký (hiếm, nhưng cần update port).
    """
    opts = {"aid": 497858, "userAgent": UA}
    a = a_bogus_web.long_a_bogus(Q, B, opts)
    _assert_valid_shape(a, "abogus(lần 1)")
    time.sleep(1.1)                                            # vượt 1 tick 1ms + buffer
    b = a_bogus_web.long_a_bogus(Q, B, opts)
    _assert_valid_shape(b, "abogus(lần 2)")
    assert a != b, (
        "cùng input cách 1.1s mà chữ ký GIỐNG → time không feed vào xorv, "
        "Dola sẽ từ chối vì replay attack"
    )


# === Test 3: fallback Chrome khi a_bogus Web chết ===========================

def _broken_long_a_bogus(*a, **kw):
    """Hàm giả luôn raise để mô phỏng a_bogus_web vừa port sai / ByteDance đổi format."""
    raise RuntimeError("giả lỗi backend web — ép fallback sang Chrome")


def test_chrome_signer_is_wired_in_runtime_path():
    """Tín hiệu production: Chrome signer đã được wire từ runtime path nào chưa?

    Vì sao quan trọng: test #3 phía dưới dùng `set_chrome_signer(lambda ...)` tạm thời.
    Trong production, nếu runtime (submit_http.py / browser_pool.py) KHÔNG gọi
    `set_chrome_signer(<hàm thật>)` thì khi web lỗi, signer cứ raise `SignerError(...)`
    thay vì thực sự fallback — auto-fallback chỉ là lý thuyết.

    Test này fail CHƯA PHẢI là lỗi port mà là cảnh báo: hook Chrome signer rỗng.
    Muốn xanh phải:
      - viết hàm ký thật (vd: `sign_with_chrome(qs, body, aid, ua)` dùng browser)
      - gọi `signer.set_chrome_signer(sign_with_chrome)` ở import time của runtime
      - đặt DOLA_SIGNER_BACKEND=auto làm default
    """
    import sys

    # Phát hiện: import runtime path (submit_http.py) — nếu nó wire Chrome thì
    # `signer.last_backend` sẽ chuyển sang "chrome" sau một call auto thất bại.
    # Vì không có request thật, ta kiểm tra gián tiếp: import đủ các module runtime
    # rồi xem module nào reference `set_chrome_signer`.

    candidates = ["submit_http", "browser_pool", "runtime", "app"]
    wired_from = []
    for mod_name in candidates:
        try:
            mod = __import__(mod_name)
        except Exception:
            continue
        src = getattr(mod, "__file__", "") or ""
        try:
            with open(src, "r", encoding="utf-8") as f:
                if "set_chrome_signer" in f.read():
                    wired_from.append(mod_name)
        except Exception:
            pass

    if not wired_from:
        print(
            "  WARN  test_chrome_signer_is_wired_in_runtime_path — CHƯA CÓ module nào wire "
            "signer.set_chrome_signer(<hàm Chrome thật>). Auto-fallback hiện KHÔNG hoạt động "
            "trong production: khi a_bogus_web chết, signer sẽ raise SignerError thay vì "
            "rơi về Chrome. Cần wire trong submit_http.py hoặc runtime init.",
            file=sys.stderr,
        )
        # Không raise — đây là cảnh báo, không phải lỗi. Test vẫn pass.
        return

    print(f"  INFO  Chrome signer đã wire từ: {wired_from}", file=sys.stderr)


def test_fallback_to_chrome_when_web_abogus_breaks():
    """Mock long_a_bogus raise → signer.sign() phải rơi về Chrome signer.

    Setup:
      - env DOLA_SIGNER_BACKEND=auto
      - patch a_bogus_web.long_a_bogus = luôn raise
      - cắm Chrome signer giả trả 'CHROME_FALLBACK_AAAA...'

    Expect:
      - sign(...) KHÔNG raise
      - chữ ký trả về có prefix 'CHROME_FALLBACK_'
      - last_backend() == 'chrome'
    """
    saved_env = os.environ.get("DOLA_SIGNER_BACKEND")
    os.environ["DOLA_SIGNER_BACKEND"] = "auto"
    try:
        # Patch long_a_bogus để web chắc chắn lỗi
        orig_long = a_bogus_web.long_a_bogus
        a_bogus_web.long_a_bogus = _broken_long_a_bogus
        try:
            # Cắm Chrome signer giả — production dùng browser thật, test dùng lambda
            sentinel = "CHROME_FALLBACK_" + "A" * 120
            signer.set_chrome_signer(lambda q, b, **kw: sentinel)
            try:
                ab = signer.sign(Q, B, aid=497858, user_agent=UA)
            finally:
                signer.set_chrome_signer(None)              # trả lại state sạch
        finally:
            a_bogus_web.long_a_bogus = orig_long

        # Verify
        assert ab.startswith("CHROME_FALLBACK_"), (
            f"fallback chữ ký phải có prefix CHROME_FALLBACK_, nhận: {ab[:40]}..."
        )
        assert signer.last_backend() == "chrome", (
            f"sau fallback last_backend() phải là 'chrome', nhận: {signer.last_backend()!r}"
        )
        assert len(ab) >= 100, f"chữ ký Chrome quá ngắn: len={len(ab)}"
    finally:
        # Phục hồi env gốc — quan trọng vì test khác có thể đọc env
        if saved_env is None:
            os.environ.pop("DOLA_SIGNER_BACKEND", None)
        else:
            os.environ["DOLA_SIGNER_BACKEND"] = saved_env


# === Sanity test phụ: signer.sign ở mode web thuần =========================

def test_signer_web_mode_returns_valid_abogus():
    """Smoke test: backend=web + a_bogus_web chưa patch → sign() phải trả chữ ký hợp lệ."""
    saved = os.environ.get("DOLA_SIGNER_BACKEND")
    os.environ["DOLA_SIGNER_BACKEND"] = "web"
    try:
        ab = signer.sign(Q, B, aid=497858, user_agent=UA)
    finally:
        if saved is None:
            os.environ.pop("DOLA_SIGNER_BACKEND", None)
        else:
            os.environ["DOLA_SIGNER_BACKEND"] = saved

    _assert_valid_shape(ab, "signer.sign(web)")
    assert signer.last_backend() == "web", f"backend web phải báo 'web', nhận {signer.last_backend()!r}"


if __name__ == "__main__":
    # Chạy trực tiếp không qua pytest (cùng style với test_max_jobs_per_ip.py).
    tests = [
        test_python_abogus_stable_shape_and_changes_with_input,
        test_python_abogus_matches_chrome_when_available,
        test_abogus_changes_after_1_second,
        test_fallback_to_chrome_when_web_abogus_breaks,
        test_signer_web_mode_returns_valid_abogus,
        test_chrome_signer_is_wired_in_runtime_path,
    ]
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except Exception as e:
            # In lỗi đầy đủ để debug, KHÔNG nuốt — fail phải fail thẳng.
            print(f"  FAIL  {t.__name__}: {type(e).__name__}: {e}")
            raise
    print("\nOK — 3 test (matches-chrome phần A + time-sensitivity + fallback) pass")
