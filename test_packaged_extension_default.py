"""Bản đóng gói (.exe/.dmg) phải BẬT SẴN extension 30s.

Gói chỉ chứa `**/*.py` + `extensions/**/*`, KHÔNG có .env.local — nên nếu mặc định của
DOLA_EXTRA_EXTENSION_DIR vẫn là "" thì người dùng bản đóng gói không bao giờ có 30s qua extension,
dù thư mục extension nằm ngay trong gói.

Vì sao test đọc MÃ NGUỒN chứ không `import config`: trên máy dev, (a) config.cpython-*.so biên dịch cũ
che mất config.py, và (b) chính config.py nạp .env.local vào os.environ lúc import — cả hai đều làm
`config.EXTRA_EXTENSION_DIR` có giá trị đúng vì lý do KHÁC, nên import bình thường không bắt được lỗi."""
import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent


def _load_config_py(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "config.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_source_default_points_at_bundled_khan():
    """Không có biến môi trường nào (đúng như trong gói) thì mặc định phải là extensions/khan đóng kèm."""
    src = (REPO / "config.py").read_text(encoding="utf-8")
    block = re.search(r"^EXTRA_EXTENSION_DIR.*?(?=\n[A-Z_]+ *=|\n\n)", src, re.S | re.M)
    assert block, "không tìm thấy khai báo EXTRA_EXTENSION_DIR"
    assert '"extensions" / "khan"' in block.group(0), block.group(0)[:200]


def test_bundled_extension_folder_really_exists():
    khan = REPO / "extensions" / "khan"
    assert khan.is_dir() and (khan / "manifest.json").is_file()


def test_env_var_still_wins(monkeypatch):
    monkeypatch.setenv("DOLA_EXTRA_EXTENSION_DIR", "/tmp/khac")
    assert _load_config_py("config_src2").EXTRA_EXTENSION_DIR == "/tmp/khac"


def test_can_still_turn_it_off(monkeypatch):
    """Tắt hẳn: đặt biến môi trường = "0" (chuỗi rỗng nay có nghĩa "dùng mặc định", không còn là tắt)."""
    monkeypatch.setenv("DOLA_EXTRA_EXTENSION_DIR", "0")
    assert _load_config_py("config_src3").EXTRA_EXTENSION_DIR == ""
