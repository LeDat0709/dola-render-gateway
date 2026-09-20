"""Ô "Nick gửi cùng lúc" trên Studio phải là TRẦN THẬT.

20/09: giao diện ghi 5 (DOLA_MAX_CONCURRENCY) nhưng chốt thật là DOLA_MAX_JOBS_PER_IP=1 mà giao diện không
bao giờ đụng tới → mọi job xếp hàng 1 lượt, ~6,5 phút/video, người dùng tưởng đang chạy 5 luồng.
Sửa: đổi luồng ở giao diện thì nới luôn trần theo-IP cho bằng."""
import config
import server


def test_endpoint_raises_ip_gate_to_match(monkeypatch):
    monkeypatch.setattr(config, "MAX_JOBS_PER_IP", 1, raising=False)
    assert server._sync_ip_gate(5) == 5
    assert config.MAX_JOBS_PER_IP == 5


def test_ip_gate_never_goes_below_one_job(monkeypatch):
    monkeypatch.setattr(config, "MAX_JOBS_PER_IP", 4, raising=False)
    assert server._sync_ip_gate(1) == 1
    assert config.MAX_JOBS_PER_IP == 1


def test_startup_applies_ui_value_too():
    """Khởi động lại server cũng phải theo ô giao diện: .env.local giữ DOLA_MAX_CONCURRENCY, còn
    DOLA_MAX_JOBS_PER_IP=1 cũ sẽ lại siết về 1 nếu lúc khởi động không đồng bộ."""
    assert config.MAX_JOBS_PER_IP == 0 or config.MAX_JOBS_PER_IP == max(1, config.MAX_CONCURRENCY)


def test_unlimited_ip_gate_stays_unlimited(monkeypatch):
    """0 = KHÔNG giới hạn theo IP (người dùng tự đặt trong .env.local) → đừng siết lại thành số."""
    monkeypatch.setattr(config, "MAX_JOBS_PER_IP", 0, raising=False)
    assert server._sync_ip_gate(5) == 0
    assert config.MAX_JOBS_PER_IP == 0
