"""Self-check for Facebook auto-login builders in facebook_login.py.
Run: python test_fb_autofill.py
"""
import inspect
import facebook_login as F


def test_login_js_injects_and_keeps_selectors():
    js = F._fill_login_js("100093|weird\"'`", "p@ss\"word")
    assert "100093" in js and "p@ss" in js.replace("\\", "")
    assert 'input[type="password"]' in js and "sai-mat-khau" in js
    assert js.count("(() =>") == 1  # single IIFE, quotes did not break out


def test_2fa_js_injects_code_and_field():
    two = F._fill_2fa_js("123456")
    assert '"123456"' in two and "approvals_code" in two


def test_autofill_requires_credentials():
    src = inspect.getsource(F._fb_autofill_login)
    assert "if not (uid and password):" in src
    assert 'return "no-creds"' in src


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("ALL PASS")
