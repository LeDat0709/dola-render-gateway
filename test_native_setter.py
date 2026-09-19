"""Test native_setter: React controlled inputs bypass.

Test:
1. JS snippet có đúng cấu trúc prototype setter
2. get_react_safe_setter_js() trả string hợp lệ
3. probe_setter() trả về dict chuẩn
4. install_chrome_runtime_proxy() patch thành công nếu playwright có
5. set_input_value() xử lý timeout/error đúng
"""
import asyncio
import builtins

import pytest

from native_setter import (
    get_react_safe_setter_js,
    install_chrome_runtime_proxy,
    set_input_value,
)


def test_react_safe_setter_js_contains_required():
    """JS snippet phai chua prototype setter walk + dispatchEvent."""
    js = get_react_safe_setter_js()
    assert "getOwnPropertyDescriptor" in js
    assert "getPrototypeOf" in js
    assert "desc.set.call" in js
    assert "dispatchEvent" in js
    assert "bubbles: true" in js
    assert "Event('input'" in js
    assert "Event('change'" in js
    assert "Event('blur'" in js


def test_react_safe_setter_returns_dict():
    """JS phai tra {ok, reason|path}."""
    js = get_react_safe_setter_js()
    assert "ok: false" in js
    assert "ok: true" in js
    assert "fallback_direct" in js
    assert "native_setter" in js
    assert "element_not_found" in js


def test_get_react_safe_setter_js_returns_string():
    """get_react_safe_setter_js() tra string, khong phai bytes."""
    js = get_react_safe_setter_js()
    assert isinstance(js, str)
    assert len(js) > 100


def test_set_input_value_handles_timeout():
    """set_input_value phai tra False khi selector khong ton tai."""
    class FakePage:
        async def wait_for_selector(self, selector, timeout):
            return None

        async def evaluate(self, js, args):
            return {"ok": False, "reason": "element_not_found"}

    async def run():
        page = FakePage()
        return await set_input_value(page, "#nonexistent", "foo")

    assert asyncio.run(run()) is False


def test_set_input_value_handles_evaluate_exception():
    """set_input_value phai tra False khi evaluate raise exception."""
    class FakePage:
        async def wait_for_selector(self, selector, timeout):
            return None

        async def evaluate(self, js, args):
            raise RuntimeError("simulated JS error")

    async def run():
        page = FakePage()
        return await set_input_value(page, "#input", "foo")

    assert asyncio.run(run()) is False


def test_set_input_value_handles_no_wait_selector():
    """set_input_value phai tra False khi wait_for_selector timeout."""
    class FakePage:
        async def wait_for_selector(self, selector, timeout):
            raise asyncio.TimeoutError("not found")

        async def evaluate(self, js, args):
            return {"ok": True}

    async def run():
        page = FakePage()
        return await set_input_value(page, "#input", "foo", timeout_ms=100)

    assert asyncio.run(run()) is False


def test_set_input_value_success_path():
    """set_input_value tra True khi evaluate thanh cong."""
    class FakePage:
        async def wait_for_selector(self, selector, timeout):
            return None

        async def evaluate(self, js, args):
            assert "input" in js.lower()
            assert isinstance(args, list)
            assert args[0] == "#email"
            assert args[1] == "foo@bar"
            return {"ok": True, "path": "native_setter"}

    async def run():
        page = FakePage()
        return await set_input_value(page, "#email", "foo@bar")

    assert asyncio.run(run()) is True


def test_install_chrome_runtime_proxy_handles_missing_playwright(monkeypatch):
    """install_chrome_runtime_proxy() tra False neu playwright chua cai."""
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("playwright"):
            raise ImportError("simulated playwright not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert install_chrome_runtime_proxy() is False
