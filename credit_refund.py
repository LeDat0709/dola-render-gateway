"""Auto-hoàn điểm (auto-refund) cho job bị lỗi trước khi Dola thật sự trừ credit.

Học từ đối thủ Seedance AI Studio v1.0.93 (account_store.hoan_diem_neu_co):
  - Nếu lỗi xảy ra TRƯỚC khi Dola trừ credit (HTTP 4xx, a_bogus fail, mở Chrome fail)
  → trả lại 1 lượt cho grant, không tính vào used_today
  - Nếu lỗi xảy ra SAU khi Dola đã nhận (HTTP 200 có conv_id) → KHÔNG hoàn

Phân loại refund theo failure_code:

  refund_yes (lỗi TRƯỚC gửi, an toàn hoàn):
    - submit_4xx_no_conv_id      : 710022002 trả 4xx — chắc chắn chưa gửi
    - submit_rate_limited        : SubmitHttpRateLimited maybe_delivered=False
    - a_bogus_signature_failed   : chữ ký lệch → Dola từ chối
    - proxy_unreachable          : proxy rớt → không tới Dola
    - chrome_open_failed         : không mở được Chrome
    - cookie_load_failed         : cookies.json hỏng
    - account_cooldown           : nick đang nghỉ → không gửi
    - rate_limit_retry_exhausted : chờ hết mốc vẫn dính 710022002

  refund_no (lỗi SAU gửi, KHÔNG hoàn):
    - captcha_blocked            : Dola đã nhận + đang chặn risk
    - video_already_built        : job đã hoàn tất
    - credit_grant_exhausted     : hết credit grant
    - user_cancelled             : user huỷ
    - download_timeout           : đã trừ, tải hỏng
    - video_too_long             : Dola từ chối spec
    - checkpoint_facebook        : Dola đã trừ + checkpoint
    - cookie_dead                : nick chết
    - insufficient_balance       : hết credit nick

Public API:
    CreditLedger.record_spent(grant_id, job_id, amount) → spent_id
    CreditLedger.record_refund(grant_id, job_id, reason, amount=1) → refund_id
    CreditLedger.balance(grant_id) → int
    CreditLedger.summary() → dict
    CreditLedger.should_refund(failure_code) → bool

Schema:
    SQLite table credit_ledger:
      id INTEGER PRIMARY KEY
      grant_id TEXT NOT NULL
      job_id TEXT
      kind TEXT NOT NULL  ('spent' | 'refund')
      amount INTEGER NOT NULL DEFAULT 1
      reason TEXT
      failure_code TEXT
      created_at REAL
      INDEX (grant_id, created_at)
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

# Failure codes MÀ ĐỐI THỦ XÁC NHẬN ĐƯỢC HOÀN (chưa trừ credit thật)
_REFUNDABLE_FAILURES: set[str] = {
    "submit_4xx_no_conv_id",
    "submit_rate_limited",
    "a_bogus_signature_failed",
    "proxy_unreachable",
    "chrome_open_failed",
    "cookie_load_failed",
    "account_cooldown",
    "rate_limit_retry_exhausted",
}

# Failure codes KHÔNG HOÀN (đã trừ hoặc lỗi không liên quan credit)
_NON_REFUNDABLE_FAILURES: set[str] = {
    "captcha_blocked",
    "video_already_built",
    "credit_grant_exhausted",
    "user_cancelled",
    "download_timeout",
    "video_too_long",
    "checkpoint_facebook",
    "cookie_dead",
    "insufficient_balance",
}


class CreditLedger:
    """Sổ cái credit, theo dõi spent vs refund theo grant.

    Singleton: gọi CreditLedger.instance() để lấy instance duy nhất.
    Thread-safe: dùng threading.Lock cho SQLite.
    """

    _instance: "CreditLedger | None" = None
    _lock_singleton = threading.Lock()

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    @classmethod
    def instance(cls, db_path: Path | None = None) -> "CreditLedger":
        """Lấy singleton; khởi tạo lần đầu với db_path."""
        with cls._lock_singleton:
            if cls._instance is None:
                if db_path is None:
                    db_path = Path("tasks.db")
                cls._instance = cls(db_path)
            return cls._instance

    @classmethod
    def reset_for_tests(cls) -> None:
        """Test helper: xoá singleton."""
        with cls._lock_singleton:
            cls._instance = None

    def _init_db(self) -> None:
        with self._lock:
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            try:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS credit_ledger (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        grant_id TEXT NOT NULL,
                        job_id TEXT,
                        kind TEXT NOT NULL CHECK (kind IN ('spent','refund')),
                        amount INTEGER NOT NULL DEFAULT 1,
                        reason TEXT,
                        failure_code TEXT,
                        created_at REAL NOT NULL
                    )
                    """
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_credit_grant "
                    "ON credit_ledger (grant_id, created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_credit_job "
                    "ON credit_ledger (job_id)"
                )
                conn.commit()
            finally:
                conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), check_same_thread=False)

    def record_spent(
        self, grant_id: str, job_id: str = "", amount: int = 1, reason: str = ""
    ) -> int:
        """Ghi nhận đã tiêu credit. Trả về ledger_id."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO credit_ledger "
                    "(grant_id, job_id, kind, amount, reason, created_at) "
                    "VALUES (?, ?, 'spent', ?, ?, ?)",
                    (grant_id, job_id, amount, reason, time.time()),
                )
                conn.commit()
                return cur.lastrowid or 0
            finally:
                conn.close()

    def record_refund(
        self,
        grant_id: str,
        job_id: str = "",
        failure_code: str = "",
        amount: int = 1,
        reason: str = "",
    ) -> int | None:
        """Ghi nhận hoàn credit. Trả None nếu failure_code không được hoàn.

        Idempotent: gọi 2 lần cùng (grant_id, failure_code) chỉ hoàn 1 lần.
        Chrome engine (job_id=""): hoàn ngay không cần spent record vì Chrome chưa trừ credit.
        HTTP engine (job_id!= ""): cần spent record tồn tại để xác nhận đã trừ rồi mới hoàn."""
        if not self.should_refund(failure_code):
            return None
        reason = reason or f"refund:{failure_code}"
        with self._lock:
            conn = self._connect()
            try:
                if job_id:
                    # HTTP engine: cần spent record tồn tại
                    cur = conn.execute(
                        "SELECT id FROM credit_ledger "
                        "WHERE job_id=? AND kind='spent' AND amount>0 "
                        "ORDER BY id DESC LIMIT 1",
                        (job_id,),
                    )
                    if not cur.fetchone():
                        return None
                    # Check chưa refund theo job_id
                    cur2 = conn.execute(
                        "SELECT id FROM credit_ledger "
                        "WHERE job_id=? AND kind='refund'",
                        (job_id,),
                    )
                    if cur2.fetchone():
                        return None  # Đã hoàn rồi
                else:
                    # Chrome engine (job_id=""): idempotent theo grant_id + failure_code + reason. Nơi gọi truyền
                    # reason riêng cho mỗi lần chạy job (browser_pool: "chrome:<attempt>") → job sau cùng lỗi vẫn
                    # được hoàn; không truyền reason thì reason mặc định giống nhau → như cũ (1 lần / nick / mã).
                    cur = conn.execute(
                        "SELECT id FROM credit_ledger "
                        "WHERE grant_id=? AND kind='refund' AND failure_code=? AND reason=?",
                        (grant_id, failure_code, reason),
                    )
                    if cur.fetchone():
                        return None  # Đã hoàn rồi
                # Ghi refund
                cur3 = conn.execute(
                    "INSERT INTO credit_ledger "
                    "(grant_id, job_id, kind, amount, reason, failure_code, created_at) "
                    "VALUES (?, ?, 'refund', ?, ?, ?, ?)",
                    (
                        grant_id,
                        job_id,
                        amount,
                        reason,
                        failure_code,
                        time.time(),
                    ),
                )
                conn.commit()
                return cur3.lastrowid or 0
            finally:
                conn.close()

    def balance(self, grant_id: str) -> int:
        """Tính balance: total_spent - total_refund (theo grant_id)."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT "
                    "COALESCE(SUM(CASE WHEN kind='spent' THEN amount ELSE 0 END), 0) AS spent, "
                    "COALESCE(SUM(CASE WHEN kind='refund' THEN amount ELSE 0 END), 0) AS refunded "
                    "FROM credit_ledger WHERE grant_id=?",
                    (grant_id,),
                )
                row = cur.fetchone()
                if not row:
                    return 0
                spent, refunded = row
                return max(0, int(spent) - int(refunded))
            finally:
                conn.close()

    def summary(self) -> dict:
        """Tóm tắt toàn bộ ledger (debug/admin)."""
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "SELECT "
                    "COUNT(*) AS total_rows, "
                    "SUM(CASE WHEN kind='spent' THEN amount ELSE 0 END) AS total_spent, "
                    "SUM(CASE WHEN kind='refund' THEN amount ELSE 0 END) AS total_refunded, "
                    "COUNT(DISTINCT grant_id) AS grants, "
                    "COUNT(DISTINCT job_id) AS jobs "
                    "FROM credit_ledger"
                )
                row = cur.fetchone()
                if not row:
                    return {
                        "total_rows": 0,
                        "total_spent": 0,
                        "total_refunded": 0,
                        "net_spent": 0,
                        "grants": 0,
                        "jobs": 0,
                    }
                total_rows, total_spent, total_refunded, grants, jobs = row
                return {
                    "total_rows": int(total_rows or 0),
                    "total_spent": int(total_spent or 0),
                    "total_refunded": int(total_refunded or 0),
                    "net_spent": int((total_spent or 0) - (total_refunded or 0)),
                    "grants": int(grants or 0),
                    "jobs": int(jobs or 0),
                }
            finally:
                conn.close()

    def list_refunds(self, limit: int = 50) -> list[dict]:
        """Danh sách refund gần nhất (admin debug)."""
        with self._lock:
            conn = self._connect()
            try:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    "SELECT * FROM credit_ledger "
                    "WHERE kind='refund' "
                    "ORDER BY id DESC LIMIT ?",
                    (limit,),
                )
                return [dict(r) for r in cur.fetchall()]
            finally:
                conn.close()

    @staticmethod
    def should_refund(failure_code: str) -> bool:
        """Quyết định có hoàn credit không dựa trên failure_code.

        Đối thủ chỉ hoàn khi CHẮC CHẮN Dola chưa trừ credit:
          - HTTP 4xx + 710022002 → chưa trừ (response trước khi tính)
          - a_bogus fail → chưa gửi tới Dola
          - proxy/proxy lỗi → chưa gửi

        KHÔNG hoàn khi:
          - HTTP 200 (Dola đã nhận)
          - captcha/checkpoint (Dola đã trừ + đang chặn)
        """
        if not failure_code:
            return False
        # Check non-refundable TRƯỚC: register_non_refundable phải có tác dụng
        # ngay cả khi code đó cũng có trong _REFUNDABLE_FAILURES (override).
        if failure_code in _NON_REFUNDABLE_FAILURES:
            return False
        if failure_code in _REFUNDABLE_FAILURES:
            return True
        # Mặc định: AN TOÀN, không hoàn (tránh hoàn oan)
        return False

    @staticmethod
    def register_refundable(failure_code: str) -> None:
        """Đăng ký failure_code mới được phép hoàn (runtime)."""
        _REFUNDABLE_FAILURES.add(failure_code)

    @staticmethod
    def register_non_refundable(failure_code: str) -> None:
        """Đăng ký failure_code KHÔNG được hoàn (runtime)."""
        _NON_REFUNDABLE_FAILURES.add(failure_code)
