"""Dola dựng xong nhưng tải về hỏng: KHÔNG được coi là hoàn tất, và phải tự tải lại bằng URL MỚI.

Link video của Dola có chữ ký HẾT HẠN. Trước đây tải hỏng thì job vẫn ghi "completed" kèm link CDN — người
dùng tưởng video đã về máy, tới lúc bấm thì link chết, mất trắng dù đã trừ lượt. Và luồng tự cứu bỏ cuộc ngay
sau một lần tải hỏng thay vì quét lại URL mới ở mốc sau.
"""
import asyncio
import tempfile
import time
from pathlib import Path

import server
from store import TaskStore


def _store_tam(d):
    s = TaskStore(str(Path(d) / "t.db"))
    server.store = s
    return s


def test_tai_hong_thi_khong_goi_la_hoan_tat():
    """_public_video_url trả link CDN khi thiếu file local — nên KHÔNG được dùng nó để ghi completed."""
    cdn = "https://cdn.dola.com/v.mp4?sign=abc"
    assert server._public_video_url({"local_path": "", "video_url": cdn}) == cdn
    assert "/videos/" in server._public_video_url({"local_path": "/tmp/a.mp4", "video_url": cdn})


def test_cuu_video_khong_bo_cuoc_sau_mot_lan_tai_hong():
    """Tải hỏng ở mốc đầu → phải ĐI TIẾP mốc sau với URL mới, chứ không dừng hẳn."""
    with tempfile.TemporaryDirectory() as d:
        s = _store_tam(d)
        s.create("t1", "seedance-2.5", "con mèo", "9:16", 10, account="n1")
        s.update("t1", status="failed", failure_code="download_failed",
                 conversation_id="777", submitted_at=time.time())

        quet, tai = [], []

        async def scan(account, limit=20):
            quet.append(account)
            # mỗi lần quét trả URL MỚI (chữ ký mới)
            return [{"conversation_id": "777", "created_at": int(time.time()),
                     "video_url": f"https://cdn/v.mp4?sign={len(quet)}", "name": "犬舍繁育者视频"}]

        async def download(url, account, prompt):
            tai.append(url)
            if len(tai) == 1:
                raise RuntimeError("ContentLengthError: Not enough data")   # lượt đầu hỏng
            f = Path(d) / "v.mp4"; f.write_bytes(b"x" * 1024)
            return f

        import video_worker_ui, video_worker
        video_worker_ui.scan_account_videos = scan
        video_worker._download = download
        server.CUU_VIDEO_SAU = (0, 0)          # khỏi chờ thật trong test

        asyncio.run(server._cuu_video_da_tra_luot("t1", "n1", "con mèo", time.time()))

        assert len(tai) == 2, f"phải thử lại ở mốc sau, chỉ thử {len(tai)} lần"
        assert tai[0] != tai[1], "mốc sau phải dùng URL MỚI quét lại, không lặp URL cũ đã hết hạn"
        row = s.get("t1")
        assert row["status"] == "completed", f"tải được rồi phải thành completed, đang là {row['status']}"
        assert "/videos/" in row["video_url"], "phải trỏ về file trên máy, không phải link CDN"


def test_da_hoan_tat_thi_thoi_cuu():
    """Người dùng tự quét tải về rồi thì luồng cứu phải dừng, không tải chồng."""
    with tempfile.TemporaryDirectory() as d:
        s = _store_tam(d)
        s.create("t2", "seedance-2.5", "p", "9:16", 10, account="n1")
        s.update("t2", status="completed", video_url="/videos/xong.mp4")
        goi = []

        async def scan(account, limit=20):
            goi.append(account); return []

        import video_worker_ui
        video_worker_ui.scan_account_videos = scan
        server.CUU_VIDEO_SAU = (0,)
        asyncio.run(server._cuu_video_da_tra_luot("t2", "n1", "p", time.time()))
        assert not goi, "job đã xong mà vẫn đi quét"


def test_khong_gan_video_cua_job_khac():
    """Ghép theo conversation_id của chính job; hội thoại lạ không được nhận."""
    row = {"conversation_id": "777"}
    ds = [{"conversation_id": "999", "created_at": int(time.time()), "video_url": "u"},
          {"conversation_id": "777", "created_at": int(time.time()), "video_url": "dung"}]
    assert server._chon_video(ds, row, time.time())["video_url"] == "dung"
    assert server._chon_video([ds[0]], row, time.time()) is None


def test_hai_job_cung_nick_khong_nhat_trung_mot_video():
    """Job A đang TẢI video của hội thoại X thì job B quét cùng nick: X phải đã có chủ, B không được nhặt lại.

    Trước đây conversation_id chỉ ghi vào store SAU khi tải xong, nên suốt lúc tải hội thoại X vẫn trống chỗ
    với store.task_by_conversation → hai job cùng nhận một video, một job báo xong bằng video của job kia.
    """
    with tempfile.TemporaryDirectory() as d:
        s = _store_tam(d)
        for t in ("tA", "tB"):
            s.create(t, "seedance-2.5", "p", "9:16", 10, account="n1")
            s.update(t, status="failed", failure_code="submit_unconfirmed", submitted_at=time.time())
        luc_gui = time.time()
        ds = [{"conversation_id": "555", "created_at": int(luc_gui), "video_url": "u", "name": "x"}]

        # A chọn được X và nhận chỗ ngay (chưa tải xong → chưa completed)
        v = server._chon_video(ds, s.get("tA"), luc_gui)
        assert v and v["conversation_id"] == "555"
        s.update("tA", conversation_id=v["conversation_id"])

        # B quét giữa lúc A còn đang tải: không được thấy X là hàng vô chủ nữa
        assert server._chon_video(ds, s.get("tB"), luc_gui) is None, "hai job cùng nhặt một video"


if __name__ == "__main__":
    test_tai_hong_thi_khong_goi_la_hoan_tat()
    test_cuu_video_khong_bo_cuoc_sau_mot_lan_tai_hong()
    test_hai_job_cung_nick_khong_nhat_trung_mot_video()
    test_da_hoan_tat_thi_thoi_cuu()
    test_khong_gan_video_cua_job_khac()
    print("OK")
