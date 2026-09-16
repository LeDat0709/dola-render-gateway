"""Ảnh tham chiếu: chặn DNS rebinding tới mạng nội bộ / metadata máy chủ.

Lỗ cũ: kiểm IP một lần (validate_public_url), rồi aiohttp TỰ PHÂN GIẢI LẠI tên miền lúc nối. Máy chủ DNS của kẻ
tấn công trả IP công cộng cho lần kiểm và 169.254.169.254 (metadata) cho lần nối → gateway tự đi lấy dữ liệu nội
bộ hộ họ. Nay resolver kiểm ngay tại lần phân giải dùng để nối, nên không còn lần thứ hai để tráo.
"""
import asyncio

import media


def test_public_vs_private_ip_classification():
    for good in ("8.8.8.8", "1.1.1.1", "93.184.216.34"):
        assert media._is_public_ip(good), good
    for bad in ("127.0.0.1", "10.0.0.5", "192.168.1.1", "172.16.0.1", "169.254.169.254",
                "0.0.0.0", "::1", "fc00::1", "224.0.0.1", "không-phải-ip", ""):
        assert not media._is_public_ip(bad), bad


class _FakeInner:
    """Giả máy phân giải DNS: trả đúng danh sách IP mình đưa vào."""

    def __init__(self, hosts):
        self.hosts = hosts

    async def resolve(self, host, port=0, family=0):
        return [{"host": h, "port": port, "family": family, "proto": 0, "flags": 0} for h in self.hosts]

    async def close(self):
        pass


def _resolve(hosts):
    resolver = media._PublicOnlyResolver()
    resolver._inner = _FakeInner(hosts)
    try:
        return [h["host"] for h in asyncio.run(resolver.resolve("example.com", 443))]
    except OSError:
        return "BLOCKED"


def test_resolver_blocks_internal_addresses():
    assert _resolve(["93.184.216.34"]) == ["93.184.216.34"], "IP công cộng phải qua"
    assert _resolve(["169.254.169.254"]) == "BLOCKED", "metadata máy chủ phải bị chặn"
    assert _resolve(["127.0.0.1"]) == "BLOCKED"
    assert _resolve(["10.1.2.3"]) == "BLOCKED"
    assert _resolve(["::1"]) == "BLOCKED"


def test_resolver_drops_internal_ip_from_mixed_answer():
    # Rebinding hay trả lẫn lộn: phải loại IP nội bộ, chỉ nối tới IP công cộng
    assert _resolve(["93.184.216.34", "169.254.169.254"]) == ["93.184.216.34"]
    assert _resolve(["10.0.0.1", "192.168.0.1"]) == "BLOCKED"


if __name__ == "__main__":
    test_public_vs_private_ip_classification()
    test_resolver_blocks_internal_addresses()
    test_resolver_drops_internal_ip_from_mixed_answer()
    print("OK")
