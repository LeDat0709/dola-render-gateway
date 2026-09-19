"""Native prototype setter - bypass React controlled inputs (học từ đối thủ).

Học từ Seedance AI Studio v1.0.93 (nick-login.cjs):
  Facebook dùng React controlled inputs. Nếu chỉ gán input.value = "...",
  React state KHÔNG cập nhật và XÓA TRẮNG ô khi render. Tool đối thủ bypass
  bằng cách can thiệp prototype setter:

    const desc = Object.getOwnPropertyDescriptor(
        Object.getPrototypeOf(el), 'value');
    if (desc && desc.set) desc.set.call(el, val);
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));

Python implementation qua Playwright page.evaluate:
  - set_input_value(page, selector, value) → auto-detect + set
  - install_chrome_runtime_proxy() → patch page.fill/page.type global

Public API:
    set_input_value(page, selector, value) -> bool
        Set giá trị qua React native setter.

    install_chrome_runtime_proxy() -> bool
        Monkey-patch Playwright page.fill/page.type để LUÔN dùng native setter.
        Sau khi gọi: page.fill(...) sẽ tự động bypass React.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# === JS snippet để bypass React controlled inputs ===
# Truyền vào page.evaluate() khi cần.
REACT_SAFE_SETTER_JS = r"""
({selector, value, eventType}) => {
    const el = document.querySelector(selector);
    if (!el) return {ok: false, reason: 'element_not_found'};
    let proto = Object.getPrototypeOf(el);
    let desc = null;
    while (proto && !desc) {
        desc = Object.getOwnPropertyDescriptor(proto, 'value');
        proto = Object.getPrototypeOf(proto);
    }
    if (!desc || !desc.set) {
        el.value = value;
        el.dispatchEvent(new Event('input', {bubbles: true}));
        el.dispatchEvent(new Event('change', {bubbles: true}));
        return {ok: true, path: 'fallback_direct'};
    }
    desc.set.call(el, value);
    el.dispatchEvent(new Event(eventType || 'input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.dispatchEvent(new Event('blur', {bubbles: true}));
    return {ok: true, path: 'native_setter'};
}
"""


def get_react_safe_setter_js() -> str:
    """Trả về JS snippet để truyền vào page.evaluate()."""
    return REACT_SAFE_SETTER_JS


async def set_input_value(
    page: Any, selector: str, value: str, timeout_ms: int = 5000
) -> bool:
    """Set giá trị input/textarea qua native setter (bypass React).

    Args:
        page: Playwright Page (async) hoặc sync_playwright Page
        selector: CSS selector (vd: 'input[name="email"]')
        value: Giá trị cần set
        timeout_ms: Timeout chờ element xuất hiện

    Returns:
        True nếu set thành công, False nếu element không tìm thấy.
    """
    try:
        await page.wait_for_selector(selector, timeout=timeout_ms)
        result = await page.evaluate(
            """(args) => {
                const [selector, value] = args;
                const el = document.querySelector(selector);
                if (!el) return {ok: false, reason: 'element_not_found'};
                let proto = Object.getPrototypeOf(el);
                let desc = null;
                while (proto && !desc) {
                    desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    proto = Object.getPrototypeOf(proto);
                }
                if (!desc || !desc.set) {
                    el.value = value;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {ok: true, path: 'fallback_direct'};
                }
                desc.set.call(el, value);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
                return {ok: true, path: 'native_setter'};
            }""",
            [selector, value],
        )
        if not result or not result.get("ok"):
            logger.warning(
                "[native_setter] selector=%s failed: %s",
                selector, result.get("reason") if result else "no result",
            )
            return False
        path = result.get("path")
        if path == "fallback_direct":
            logger.info(
                "[native_setter] %s = fallback (no prototype setter)", selector
            )
        else:
            logger.debug("[native_setter] %s OK via native_setter", selector)
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[native_setter] %s error: %s", selector, str(e)[:120]
        )
        return False


def set_input_value_sync(
    page: Any, selector: str, value: str, timeout_ms: int = 5000
) -> bool:
    """Sync version cho sync_playwright API."""
    try:
        page.wait_for_selector(selector, timeout=timeout_ms)
        result = page.evaluate(
            """(args) => {
                const [selector, value] = args;
                const el = document.querySelector(selector);
                if (!el) return {ok: false, reason: 'element_not_found'};
                let proto = Object.getPrototypeOf(el);
                let desc = null;
                while (proto && !desc) {
                    desc = Object.getOwnPropertyDescriptor(proto, 'value');
                    proto = Object.getPrototypeOf(proto);
                }
                if (!desc || !desc.set) {
                    el.value = value;
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    return {ok: true, path: 'fallback_direct'};
                }
                desc.set.call(el, value);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
                el.dispatchEvent(new Event('blur', {bubbles: true}));
                return {ok: true, path: 'native_setter'};
            }""",
            [selector, value],
        )
        return bool(result and result.get("ok"))
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "[native_setter sync] %s error: %s", selector, str(e)[:120]
        )
        return False


def install_chrome_runtime_proxy() -> bool:
    """Monkey-patch playwright page.fill/page.type để LUÔN dùng native setter.

    Sau khi gọi:
        await page.fill('input[name="email"]', 'foo@bar')  # -> native_setter
        await page.type('textarea#prompt', 'hello')         # -> native_setter
    """
    try:
        from playwright.async_api import Page as AsyncPage
        from playwright.sync_api import Page as SyncPage
    except ImportError:
        logger.warning(
            "[native_setter] playwright chưa cài -> không patch được"
        )
        return False

    _orig_async_fill = AsyncPage.fill
    _orig_sync_fill = SyncPage.fill

    async def _async_fill(self, selector, value, **kwargs):
        ok = await set_input_value(
            self, selector, value, kwargs.get("timeout", 5000)
        )
        if ok:
            return None
        return await _orig_async_fill(self, selector, value, **kwargs)

    def _sync_fill(self, selector, value, **kwargs):
        ok = set_input_value_sync(
            self, selector, value, kwargs.get("timeout", 5000)
        )
        if ok:
            return None
        return _orig_sync_fill(self, selector, value, **kwargs)

    AsyncPage.fill = _async_fill
    SyncPage.fill = _sync_fill
    logger.info(
        "[native_setter] đã patch Playwright fill -> native_setter"
    )
    return True


# === ProseMirror Direct Transaction Injection (Seedance Studio Pro v1.1) ===
PROSEMIRROR_INJECT_JS = r"""
({text, autoSubmit}) => {
    function findActiveEditorElement() {
        const allProse = Array.from(document.querySelectorAll('.tiptap.ProseMirror, .ProseMirror, div[contenteditable="true"]'));
        const bottomProse = allProse.filter((el) => {
            if (el.closest('header, nav, aside, [class*="sidebar"]')) return false;
            const r = el.getBoundingClientRect();
            return r.height > 15 && r.width > 80 && r.top > window.innerHeight * 0.35;
        });
        if (bottomProse.length > 0) {
            bottomProse.sort((a, b) => b.getBoundingClientRect().top - a.getBoundingClientRect().top);
            return bottomProse[0];
        }
        return null;
    }

    function findEditorView(el) {
        if (!el) return null;
        let curr = el;
        while (curr && curr !== document.body) {
            if (curr.pmViewDesc && curr.pmViewDesc.editorView) {
                return curr.pmViewDesc.editorView;
            }
            curr = curr.parentElement;
        }
        const child = el.querySelector?.('.ProseMirror');
        if (child && child.pmViewDesc && child.pmViewDesc.editorView) {
            return child.pmViewDesc.editorView;
        }
        return null;
    }

    function findSendButton() {
        const exact = document.getElementById('flow-end-msg-send') ||
                      document.querySelector('#flow-end-msg-send') ||
                      document.querySelector('button[class*="send-msg-btn"]') ||
                      document.querySelector('[class*="send-msg-btn"]') ||
                      document.querySelector('.send-btn-wrapper button') ||
                      document.querySelector('[data-testid*="send"]') ||
                      document.querySelector('button[aria-label*="send" i]') ||
                      document.querySelector('button[aria-label*="gửi" i]') ||
                      document.querySelector('[id*="msg-send"]');
        if (exact) return exact.closest('button') || exact;

        const input = findActiveEditorElement();
        if (input) {
            const composerBox = input.closest('form, [class*="composer"], [class*="chat-input"], [class*="input-wrap"], [class*="relative"]') || input.parentElement?.parentElement;
            if (composerBox) {
                const composerButtons = Array.from(composerBox.querySelectorAll('button, div[role="button"]'));
                const sendCandidate = composerButtons.reverse().find((btn) => {
                    const r = btn.getBoundingClientRect();
                    if (r.width < 15 || r.height < 15) return false;
                    if (btn.closest('[class*="exit"], .bg-g-exit-skill-btn-bg')) return false;
                    const t = (btn.innerText || btn.textContent || '').trim().toLowerCase();
                    if (/^(?:fast|pro|nhanh|2\.0|2\.5|5s|10s|15s|30s|ratio|tỉ\s*lệ|skills?|kỹ\s*năng|dự\s*án|\+|\.\.\.)$/i.test(t)) return false;
                    return btn.querySelector('svg') || /send|submit|gửi/i.test(btn.className + (btn.id || ''));
                });
                if (sendCandidate) return sendCandidate.closest('button') || sendCandidate;
            }
        }
        return null;
    }

    try {
        const editorEl = findActiveEditorElement();
        if (!editorEl) {
            return {ok: false, reason: 'editor_not_found'};
        }
        editorEl.focus();
        const view = findEditorView(editorEl);
        if (view && view.state && view.dispatch) {
            const { state, dispatch } = view;
            const tr = state.tr;
            tr.delete(0, state.doc.content.size);
            tr.insertText(String(text || ''));
            dispatch(tr);
            editorEl.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: text }));
        } else {
            editorEl.textContent = String(text || '');
            editorEl.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: text }));
        }

        if (autoSubmit) {
            setTimeout(() => {
                const btn = findSendButton();
                if (btn) btn.click();
                else {
                    const enterProps = { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true, cancelable: true };
                    editorEl.dispatchEvent(new KeyboardEvent('keydown', enterProps));
                    editorEl.dispatchEvent(new KeyboardEvent('keyup', enterProps));
                }
            }, 300);
        }
        return {ok: true, method: view ? 'prosemirror_tr' : 'dom_fallback'};
    } catch (e) {
        return {ok: false, error: String(e)};
    }
}
"""


async def inject_prosemirror_prompt(page: Any, text: str, auto_submit: bool = False) -> dict:
    """Bơm prompt siêu tốc vào ProseMirror editor của Dola bằng transaction trực tiếp.
    Học từ Seedance Studio Pro v1.1 page-bridge.js.
    """
    try:
        return await page.evaluate(
            PROSEMIRROR_INJECT_JS,
            {"text": text, "autoSubmit": auto_submit},
            isolated_context=False,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

