"""Test API trên một bản server CÔ LẬP (db + accounts trong thư mục tạm).

Không đụng nick/video thật, không gọi Dola, không tốn credit. Chạy:
    .venv/bin/python test_api_smoke.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"
ROOT = Path(__file__).resolve().parent


def call(method: str, path: str, body=None):
    """Trả (status, dữ liệu). Không raise để test đọc được cả mã lỗi."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw[:200]
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw[:200]


def start_server(tmp: Path):
    env = {**os.environ, "DOLA_PORT": str(PORT), "DOLA_HOST": "127.0.0.1",
           "DOLA_DB_PATH": str(tmp / "tasks.db"),
           "DOLA_ACCOUNTS_DIR": str(tmp / "accounts"),
           "DOLA_DOWNLOAD_DIR": str(tmp / "downloads"),
           "DOLA_API_KEYS": "", "DOLA_ADMIN_KEY": ""}
    (tmp / "accounts").mkdir(parents=True, exist_ok=True)
    # cwd = thư mục dữ liệu, mã nguồn ở chỗ khác qua --app-dir: giống y bản đóng gói .exe
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "server:app", "--host", "127.0.0.1",
                             "--port", str(PORT), "--app-dir", str(ROOT)], cwd=tmp, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(40):
        time.sleep(0.5)
        try:
            if call("GET", "/health")[0] == 200:
                return proc
        except Exception:
            pass
    proc.kill()
    raise RuntimeError("server không lên: " + proc.stderr.read().decode()[-600:])


CASES = []


def check(name, ok, detail=""):
    CASES.append((ok, name, detail))
    print(("  PASS " if ok else "  FAIL ") + name + (f" — {detail}" if detail and not ok else ""))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="dola-smoke-"))
    # app chạy với cwd = thư mục dữ liệu, mã nguồn nằm chỗ khác (giống bản đóng gói)
    for f in ("server.py",):
        assert (ROOT / f).exists()
    env_src = ROOT
    sys.path.insert(0, str(env_src))
    proc = None
    try:
        proc = start_server(tmp)
        print("Server cô lập đã lên:", BASE)

        st, h = call("GET", "/health")
        check("/health trả 200", st == 200)
        check("máy chưa có nick: accounts rỗng", h.get("accounts") == [], repr(h.get("accounts"))[:80])
        check("available=False khi 0 nick", h.get("available") is False, repr(h.get("available")))
        for k in ("max_concurrency", "login_concurrency", "http_poll", "pending_tasks"):
            check(f"/health có '{k}'", k in h)

        st, d = call("GET", "/")
        check("dashboard web trả 200", st == 200)

        st, d = call("GET", "/api/admin/accounts")
        check("/api/admin/accounts trả 200 + rỗng", st == 200 and d.get("accounts") == [], repr(d)[:80])
        st, d = call("GET", "/api/admin/tasks")
        check("/api/admin/tasks trả 200", st == 200 and "tasks" in d)
        st, d = call("GET", "/api/report")
        check("/api/report trả 200", st == 200)

        # --- Những gì giao diện mới (Tổng quan / Kho / tab Proxy) và app ở chế độ từ xa gọi ---
        st, d = call("GET", "/api/admin/config")
        check("/api/admin/config đủ trường", st == 200 and all(k in d for k in ("proxy", "max_concurrency", "auto_retry", "max_rotate", "submit_gap", "video_timeout")), repr(d)[:100])
        (tmp / "accounts" / "n1").mkdir(parents=True, exist_ok=True)
        st, d = call("POST", "/api/admin/accounts/n1/proxy", {"proxy": "http://u:p@1.2.3.4:8080"})
        check("đặt proxy riêng cho nick", st == 200 and d.get("ok"), f"{st} {repr(d)[:80]}")
        st, d = call("GET", "/api/admin/accounts/n1/proxy")
        check("đọc lại proxy riêng (app từ xa dùng)", st == 200 and d.get("proxy") == "http://u:p@1.2.3.4:8080", repr(d)[:80])
        st, d = call("GET", "/api/admin/accounts")
        a1 = next((a for a in d.get("accounts", []) if a["name"] == "n1"), {})
        check("danh sách nick mang trường proxy", a1.get("proxy") == "http://u:p@1.2.3.4:8080", repr(a1.get("proxy")))
        missing = [k for k in ("remaining", "used_today", "limit", "cooling", "cooldown_until", "busy", "login_ok",
                               "email", "note", "scheduling", "credit_balance", "rate_limited", "quota_blocked", "last_used_at") if k not in a1]
        check("nick có đủ trường giao diện đọc", not missing, f"thiếu {missing}")
        st, d = call("GET", "/api/admin/accounts/export")
        check("xuất kho: nick chưa có cookie bị bỏ qua", st == 200 and d.get("kind") == "dola-studio-accounts" and d.get("accounts") == [] and d.get("skipped") == ["n1"], repr(d)[:120])
        (tmp / "accounts" / "n1" / "cookies.json").write_text('[{"name":"sessionid","value":"x","domain":".dola.com","path":"/"}]', encoding="utf-8")
        st, d = call("GET", "/api/admin/accounts/export")
        e1 = next((a for a in d.get("accounts", []) if a["name"] == "n1"), {})
        check("xuất kho: nick có cookie mang cookie + proxy riêng", st == 200 and e1.get("cookies") and e1.get("proxy") == "http://u:p@1.2.3.4:8080" and "scheduling" in e1, repr(e1)[:120])
        st, _ = call("POST", "/api/admin/accounts/n1/proxy", {"proxy": "khong hop le"})
        check("proxy sai định dạng → 422", st == 422, f"nhận {st}")
        st, _ = call("GET", "/api/admin/accounts/khong-ton-tai/proxy")
        check("đọc proxy nick lạ → 404", st == 404, f"nhận {st}")
        st, _ = call("POST", "/api/admin/accounts/import-cookie", {"name": "n2", "cookies": "rac", "proxy": "sai dinh dang"})
        check("import-cookie kèm proxy sai → 422 (chưa mở Chrome)", st == 422, f"nhận {st}")
        st, d = call("POST", "/api/admin/accounts/import-cookie", {"name": "n2", "cookies": "rac", "proxy": "http://u:p@1.2.3.4:8080"})
        check("cookie rác → 400 và KHÔNG để lại nick ma", st == 400 and not (tmp / "accounts" / "n2").exists(), f"nhận {st}, dir={(tmp / 'accounts' / 'n2').exists()}")
        st, _ = call("DELETE", "/api/admin/accounts/n1")
        check("xoá nick thử → pool trống lại", st == 200 and call("GET", "/api/admin/accounts")[1].get("accounts") == [], f"nhận {st}")

        st, d = call("POST", "/api/admin/concurrency", {"max_concurrency": 7, "login_concurrency": 4})
        check("đổi luồng: nhận 7/4", st == 200 and d.get("max_concurrency") == 7 and d.get("login_concurrency") == 4, repr(d)[:90])
        st, h2 = call("GET", "/health")
        check("/health phản ánh luồng mới", h2.get("max_concurrency") == 7, repr(h2.get("max_concurrency")))
        st, _ = call("POST", "/api/admin/concurrency", {"max_concurrency": 999})
        check("luồng quá trần bị từ chối (422)", st == 422, f"nhận {st}")
        st, _ = call("POST", "/api/admin/concurrency", {"max_concurrency": 0})
        check("luồng 0 bị từ chối (422)", st == 422, f"nhận {st}")

        st, _ = call("POST", "/api/admin/accounts/khong-ton-tai/wake")
        check("bỏ nghỉ nick lạ → 404", st == 404, f"nhận {st}")
        st, _ = call("PATCH", "/api/admin/accounts/khong-ton-tai", {"scheduling": True})
        check("bật lịch nick lạ → 404", st == 404, f"nhận {st}")

        st, d = call("POST", "/v1/videos/generations", {"model": "seedance-9.9", "prompt": "x"})
        check("model lạ → 422", st == 422, f"nhận {st}")
        st, d = call("POST", "/v1/videos/generations", {"model": "seedance-2.5", "prompt": "x", "duration": 7})
        check("thời lượng 7s → 422", st == 422, f"nhận {st}")
        st, d = call("POST", "/v1/videos/generations", {"model": "seedance-2.5", "prompt": "x", "account": "khong-ton-tai"})
        check("nick lạ → 422 nói rõ không tồn tại", st == 422 and "tồn tại" in str(d), f"{st} {repr(d)[:80]}")
        st, d = call("POST", "/v1/videos/generations", {"model": "seedance-2.5", "prompt": "x"})
        check("pool trống → 503 (không treo)", st == 503, f"nhận {st} {repr(d)[:60]}")
        st, d = call("POST", "/v1/videos/generations", {"model": "seedance-2.5", "prompt": ""})
        check("prompt rỗng → 422", st == 422, f"nhận {st}")

        st, d = call("GET", "/v1/videos/video_khongcothat")
        check("job lạ → 404", st == 404, f"nhận {st}")
        st, d = call("GET", "/videos/khong-co-file.mp4")
        check("file video lạ → 404", st == 404, f"nhận {st}")
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    bad = [c for c in CASES if not c[0]]
    print(f"\n{len(CASES) - len(bad)}/{len(CASES)} pass")
    if bad:
        print("LỖI:")
        for _, name, detail in bad:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
