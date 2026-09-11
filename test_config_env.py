"""Self-check: .env.local ở thư mục làm việc (DATA_DIR của bản đóng gói) phải tới được config.py.

Trước đây config.py chỉ đọc .env.local cạnh mã → trên Windows đóng gói, DOLA_PROXY người dùng
đặt trong %APPDATA%\\Dola Studio\\.env.local bị bỏ qua, Python dùng 127.0.0.1:7890 chết.
Chạy: .venv/bin/python test_config_env.py
"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent


def _proxy_seen_from(cwd: str, extra_env: dict[str, str]) -> str:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DOLA_")}
    env.update({"PYTHONPATH": str(REPO), **extra_env})
    return subprocess.run([sys.executable, "-c", "import config; print(config.PROXY)"],
                          cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout.strip()


def test_env_local_in_cwd_reaches_config():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".env.local").write_text("DOLA_PROXY=http://jp.example:3128\n", encoding="utf-8")
        got = _proxy_seen_from(d, {})
        assert got == "http://jp.example:3128", f"config.PROXY={got!r} — .env.local ở cwd bị bỏ qua"


def test_real_env_still_wins():
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".env.local").write_text("DOLA_PROXY=http://jp.example:3128\n", encoding="utf-8")
        got = _proxy_seen_from(d, {"DOLA_PROXY": "socks5://real:1080"})
        assert got == "socks5://real:1080", f"config.PROXY={got!r} — biến môi trường thật phải thắng"


if __name__ == "__main__":
    test_env_local_in_cwd_reaches_config()
    test_real_env_still_wins()
    print("OK")
