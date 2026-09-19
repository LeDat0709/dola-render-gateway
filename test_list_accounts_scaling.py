"""list_accounts() phải chạy SỐ CÂU SQL CỐ ĐỊNH, không tăng theo số nick (audit 19/9: N SELECT meta + N SELECT usage
+ N INSERT/commit mỗi lần gọi, mà generate_video gọi 3–4 lần/job và dashboard gọi 10s/lần → nghẽn event loop).

Chạy: .venv/bin/python -m pytest -q test_list_accounts_scaling.py
"""
import tempfile
from pathlib import Path

from browser_pool import BrowserPool


def _pool(tmp: str, n: int) -> BrowserPool:
    for i in range(n):
        (Path(tmp) / "accounts" / f"nick{i:02d}").mkdir(parents=True)
    return BrowserPool(accounts_dir=str(Path(tmp) / "accounts"), db_path=str(Path(tmp) / "pool.db"), max_concurrency=1)


def _sql_count(pool: BrowserPool, fn) -> int:
    stmts = []
    pool._conn.set_trace_callback(stmts.append)
    try:
        fn()
    finally:
        pool._conn.set_trace_callback(None)
    return len(stmts)


def test_list_accounts_sql_count_does_not_grow_with_nicks():
    with tempfile.TemporaryDirectory() as t5, tempfile.TemporaryDirectory() as t40:
        small, big = _pool(t5, 5), _pool(t40, 40)
        small.list_accounts(), big.list_accounts()   # lần đầu tạo dòng meta → bỏ qua
        n_small = _sql_count(small, small.list_accounts)
        n_big = _sql_count(big, big.list_accounts)
        assert n_big == n_small, (n_small, n_big)
        assert n_big <= 8, n_big


def test_list_accounts_values_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp, 3)
        pool.list_accounts()   # dòng meta tạo lười ở lần gọi đầu
        pool._conn.execute("UPDATE accounts_meta SET note='ghi chú', scheduling=0 WHERE name='nick01'")
        pool._conn.commit()
        rows = {a["name"]: a for a in pool.list_accounts()}
        assert list(rows) == ["nick00", "nick01", "nick02"]
        assert rows["nick01"]["note"] == "ghi chú" and rows["nick01"]["scheduling"] is False
        assert rows["nick00"]["scheduling"] is True and rows["nick00"]["used_today"] == 0
        # nick mới xuất hiện giữa chừng vẫn được tạo meta
        (Path(tmp) / "accounts" / "nick03").mkdir()
        assert [a["name"] for a in pool.list_accounts()][-1] == "nick03"
        assert pool._meta("nick03") is not None


def test_list_accounts_names_filter():
    with tempfile.TemporaryDirectory() as tmp:
        pool = _pool(tmp, 4)
        assert [a["name"] for a in pool.list_accounts(["nick02"])] == ["nick02"]
        assert pool.list_accounts(["khong-co"]) == []
