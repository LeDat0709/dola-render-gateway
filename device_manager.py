"""device_manager.py — Quản lý bộ định danh thiết bị (device_id, web_id, tea_uuid) của tài khoản Dola/ByteDance.

Mỗi tài khoản Dola khi giao tiếp với API /chat/completion bắt buộc phải có:
  - device_id: Chuỗi số 19 chữ số bắt đầu bằng '7' (ví dụ: '7415829104928174921')
  - web_id: Định danh phiên web của SDK (thường trùng hoặc tương thích với device_id)
  - tea_uuid: UUID telemetry gắn với thiết bị (thường là chuỗi số web_id hoặc chuỗi UUID)

Nếu thiếu device_id, server Dola sẽ coi là request mồ côi/headless bot và từ chối với lỗi 710022002.
Module này:
  1. Tải device_info.json từ accounts/<nick>/device_info.json nếu đã có.
  2. Nếu chưa có, tự động sinh bộ định danh nhất quán (deterministic) theo tên nick để cố định qua các lần chạy.
  3. Cho phép trích xuất trực tiếp từ trang web Dola thật khi mở trình duyệt và lưu lại.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Optional

import config

logger = logging.getLogger("dola.device_manager")


def _deterministic_numeric_id(seed: str, length: int = 19, prefix: str = "7") -> str:
    """Sinh chuỗi số cố định từ seed (tên nick/máy), định dạng chuỗi số 19 ký tự bắt đầu bằng '7'."""
    h = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    num = str(int(h, 16))
    needed = length - len(prefix)
    if len(num) < needed:
        num = (num * ((needed // len(num)) + 1))[:needed]
    else:
        num = num[:needed]
    return f"{prefix}{num}"


def generate_deterministic_device_info(account: str) -> dict[str, str]:
    """Sinh bộ ba định danh thiết bị nhất quán theo tên tài khoản."""
    dev_id = _deterministic_numeric_id(f"device_{account}", length=19, prefix="7")
    web_id = _deterministic_numeric_id(f"web_{account}", length=19, prefix="7")
    return {
        "device_id": dev_id,
        "web_id": web_id,
        "tea_uuid": web_id,
    }


def device_info_path(account: str) -> Path:
    """Đường dẫn tệp device_info.json của tài khoản."""
    return config.ACCOUNTS_DIR / account / "device_info.json"


def load_device_info(account: str) -> dict[str, str]:
    """Tải device_info của tài khoản. Nếu chưa có, sinh tự động và lưu lại file."""
    f = device_info_path(account)
    if f.exists():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("device_id"):
                return {
                    "device_id": str(data["device_id"]),
                    "web_id": str(data.get("web_id") or data["device_id"]),
                    "tea_uuid": str(data.get("tea_uuid") or data.get("web_id") or data["device_id"]),
                }
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Không đọc được %s (%s), sinh lại định danh mới", f, e)

    info = generate_deterministic_device_info(account)
    # Tự động lưu nếu thư mục nick đã tồn tại
    if (config.ACCOUNTS_DIR / account).is_dir():
        save_device_info(account, info)
    return info


def save_device_info(account: str, info: dict[str, str]) -> None:
    """Lưu bộ định danh vào accounts/<account>/device_info.json."""
    acc_dir = config.ACCOUNTS_DIR / account
    acc_dir.mkdir(parents=True, exist_ok=True)
    f = device_info_path(account)
    payload = {
        "device_id": str(info.get("device_id") or ""),
        "web_id": str(info.get("web_id") or info.get("device_id") or ""),
        "tea_uuid": str(info.get("tea_uuid") or info.get("web_id") or info.get("device_id") or ""),
    }
    config.atomic_write_text(f, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    logger.info("[%s] Đã lưu device_info: device_id=%s", account, payload["device_id"])


async def extract_from_page(page, account: str) -> dict[str, str]:
    """Trích xuất device_id / web_id từ localStorage hoặc request của trang Dola thật."""
    dev_id = ""
    try:
        dev_id = await page.evaluate(r"""() => {
            try {
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    const v = localStorage.getItem(k);
                    if (v && v.includes("device_id")) {
                        const m = v.match(/"device_id"\s*:\s*"?(\d+)"?/);
                        if (m) return m[1];
                    }
                }
            } catch (_) {}
            return "";
        }""")
    except Exception:
        pass

    info = load_device_info(account)
    if dev_id:
        info["device_id"] = dev_id
        info["web_id"] = dev_id
        info["tea_uuid"] = dev_id
        save_device_info(account, info)
    return info
