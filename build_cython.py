"""build_cython.py — biên dịch các file Python nhạy cảm thành .so (ẩn source).

Đối thủ Seedance AI Studio dùng Cython → .pyd để ẩn logic. Ta làm tương tự cho
các file cốt lõi:
  - signer.py          : chữ ký a_bogus (IP này = tài sản lớn nhất)
  - a_bogus_web.py     : thuật toán ký (cả năm R&D)
  - submit_http.py     : flow gửi job + bypass Dola
  - browser_pool.py    : rotation logic + fingerprint
  - config.py          : API key, signature salt

KHÔNG biên dịch:
  - server.py, video_worker_ui.py : chạy chính, cần debug nhanh
  - test_*.py                       : phải đọc được
  - store.py, broker.py             : cần hot-reload trong dev

Output:
  ./build/cython/<name>.cpython-312-darwin.so  (macOS)
  Cùng tên với file gốc để `import` không phải đổi.

Usage:
  .venv/bin/python build_cython.py
  .venv/bin/python build_cython.py --keep-source   # vẫn giữ file .py để debug

Tham khảo: seedance-v1.0.93/resources/python/*.pyd (compile ra C extension giống vậy).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "build" / "cython"

# Thứ tự quan trọng: signer + a_bogus_web phải có trước submit_http (submit_http import signer)
PRIORITY = [
    "signer.py",
    "a_bogus_web.py",
    "submit_http.py",
    "browser_pool.py",
    "config.py",
]

# Ngoài danh sách trên, cho phép biên dịch thêm (comment/uncomment tuỳ nhu cầu)
EXTRA = [
    # "store.py",
    # "media.py",
]


def _python_version_tag() -> str:
    """Trả về tag version kiểu cp312-darwin để khớp tên .so."""
    import sysconfig
    so_abi = sysconfig.get_config_var("SOABI") or "cpython-312-darwin"
    # 'cpython-312-darwin' → 'cp312-darwin'
    if so_abi.startswith("cpython-"):
        so_abi = "cp" + so_abi[len("cpython-"):]
    return so_abi


def _cythonize_one(py_file: Path, out_dir: Path) -> Path:
    """Compile 1 file .py → .so bằng Cython (compile tại chỗ, copy .so ra out_dir)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"  ⚙  cythonize: {py_file.name}")
    # Đặt tên module trùng với file .py để tránh xung đột khi import
    setup_code = f"""
from setuptools import setup
from Cython.Build import cythonize
import sys
sys.path.insert(0, r"{py_file.parent}")
setup(
    name={py_file.stem!r},
    ext_modules=cythonize(
        [r"{py_file.name!s}"],
        language_level=3,
        compiler_directives={{
            'language_level': '3',
            'embedsignature': False,
            'optimize.use_switch': True,
            'optimize.unpack_method_calls': True,
            'annotation_typing': False,
        }},
    ),
)
"""
    setup_path = out_dir / f"_setup_{py_file.stem}.py"
    setup_path.write_text(setup_code)
    try:
        subprocess.run(
            [sys.executable, str(setup_path), "build_ext", "--inplace",
             "--build-lib", str(out_dir), "--build-temp", str(out_dir / "_tmp")],
            cwd=str(py_file.parent),
            check=True,
            env={**os.environ, "PYTHONHASHSEED": "0"},
        )
    finally:
        setup_path.unlink(missing_ok=True)
    # Sau build_ext --inplace, file .so nằm cùng thư mục với .py gốc
    so_files = list(py_file.parent.glob(f"{py_file.stem}.*.so"))
    if not so_files:
        raise RuntimeError(f"Không tìm thấy .so cho {py_file.name}")
    return so_files[0]


def _drop_pyc_cache(py_file: Path) -> None:
    """Xoá __pycache__/<name>.cpython-*.pyc để Python ưu tiên load .so."""
    cache = py_file.parent / "__pycache__"
    if cache.exists():
        for p in cache.glob(f"{py_file.stem}.*.pyc"):
            p.unlink()
            print(f"  🗑  xoá cache {p.name}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep-source", action="store_true", default=True,
                    help="Giữ .py gốc sau khi compile (MẶC ĐỊNH). Dùng --no-keep-source để xoá.")
    ap.add_argument("--no-keep-source", dest="keep_source", action="store_false")
    ap.add_argument("--strict", action="store_true",
                    help="Dừng ngay khi 1 file lỗi (mặc định: bỏ qua và tiếp tục)")
    args = ap.parse_args()

    tag = _python_version_tag()
    print(f"== Cython build (tag={tag}) ==")

    targets = PRIORITY + [p for p in EXTRA if p not in PRIORITY]
    targets = [ROOT / name for name in targets if (ROOT / name).exists()]

    if not targets:
        print("❌ Không có file nào để compile. Kiểm tra PRIORITY/EXTRA trong build_cython.py")
        return 1

    backup = ROOT / "build" / "source_backup"
    backup.mkdir(parents=True, exist_ok=True)

    ok, fail = [], []
    for py in targets:
        print(f"\n→ {py.relative_to(ROOT)}")
        # Xoá .so cũ nếu có (để build sạch)
        for old_so in py.parent.glob(f"{py.stem}.*.so"):
            old_so.unlink()
            print(f"  🗑  xoá .so cũ: {old_so.name}")
        try:
            so_path = _cythonize_one(py, BUILD)
            print(f"  ✓ {so_path.relative_to(ROOT)}")
            ok.append(py)
        except subprocess.CalledProcessError as e:
            print(f"  ✗ LỖI compile {py.name}: {e}")
            fail.append(py)
            if args.strict:
                return 2
            print("  ↪ bỏ qua, tiếp tục file kế tiếp (--strict để dừng)")

        _drop_pyc_cache(py)
        if not args.keep_source:
            shutil.copy2(str(py), str(backup / py.name))
            py.unlink()
            print(f"  📦 {py.name} → build/source_backup/")
        else:
            print(f"  📝 giữ {py.name} (Python sẽ ưu tiên load .so nếu cùng tên)")

    print(f"\n✅ Compile xong: {len(ok)} OK, {len(fail)} lỗi")
    if fail:
        print("   Lỗi:", [p.name for p in fail])
    print("   Khôi phục .py: cp build/source_backup/<name>.py ./")
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
