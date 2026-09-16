"""Public reference media download and security validation (SSRF protected)."""
import asyncio
import ipaddress
import socket
import tempfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urljoin, urlparse

import aiohttp

import config

_ALLOWED_IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


def validate_public_url(url: str) -> str:
    """Accepts only public HTTP(S) URLs, blocks localhost, private network, and credentials."""
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("Invalid reference image URL")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Reference image only supports public http/https URLs")
    if parsed.username or parsed.password:
        raise ValueError("Reference image URL must not contain authentication credentials")
    host = parsed.hostname
    try:
        # SSRF check: check direct IP and DNS resolution against private/reserved ranges
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            infos = awaitable_getaddrinfo(host)
            addresses = {ipaddress.ip_address(x) for x in infos}
        except Exception as exc:
            raise ValueError(f"Failed to resolve reference image domain: {host}") from exc
    if not addresses or any(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
        for ip in addresses
    ):
        raise ValueError("Reference image points to private or reserved IP address (SSRF blocked)")
    return url


def awaitable_getaddrinfo(host: str) -> list[str]:
    """Synchronous DNS resolution wrapper."""
    return [item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)]


def _is_public_ip(raw: str) -> bool:
    try:
        ip = ipaddress.ip_address(raw)
    except ValueError:
        return False
    return not (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified)


class _PublicOnlyResolver(aiohttp.abc.AbstractResolver):
    """Chặn DNS rebinding: kiểm IP ngay ở LẦN PHÂN GIẢI aiohttp DÙNG ĐỂ NỐI.

    Kiểm trước rồi để aiohttp tự phân giải lại là một cửa sổ TOCTOU: máy chủ DNS của kẻ tấn công trả IP công cộng
    cho lần kiểm, rồi trả 169.254.169.254 (metadata máy chủ) hoặc IP nội bộ cho lần nối thật. Ở đây không có
    lần phân giải thứ hai nào ngoài lần này, nên không còn khe để tráo.
    """

    def __init__(self):
        self._inner = None   # DefaultResolver cần vòng lặp đang chạy → tạo trễ, lúc resolve

    async def resolve(self, host, port=0, family=socket.AF_INET):
        if self._inner is None:
            self._inner = aiohttp.DefaultResolver()
        hosts = await self._inner.resolve(host, port, family)
        safe = [h for h in hosts if _is_public_ip(h["host"])]
        if not safe:
            raise OSError(f"Reference image host {host} resolved to private/reserved IP (SSRF blocked)")
        return safe

    async def close(self):
        if self._inner is not None:
            await self._inner.close()


async def _validate_url_async(url: str) -> str:
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError("Invalid reference image URL")
    parsed = urlparse(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Reference image only supports public http/https URLs")
    if parsed.username or parsed.password:
        raise ValueError("Reference image URL must not contain authentication credentials")
    host = parsed.hostname
    try:
        addresses = {ipaddress.ip_address(host)}
    except ValueError:
        try:
            resolved = await asyncio.to_thread(awaitable_getaddrinfo, host)
            addresses = {ipaddress.ip_address(x) for x in resolved}
        except Exception as exc:
            raise ValueError(f"Failed to resolve reference image domain: {host}") from exc
    if not addresses or any(
        ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
        or ip.is_multicast or ip.is_unspecified
        for ip in addresses
    ):
        raise ValueError("Reference image points to private or reserved IP address (SSRF blocked)")
    return url


async def validate_reference_urls(urls: list[str]) -> list[str]:
    if len(urls) > config.REFERENCE_IMAGE_MAX_COUNT:
        raise ValueError(f"Maximum of {config.REFERENCE_IMAGE_MAX_COUNT} reference images allowed")
    normalized = []
    seen = set()
    for raw in urls:
        url = await _validate_url_async(raw)
        if url not in seen:
            normalized.append(url)
            seen.add(url)
    return normalized


async def _read_response_image(resp: aiohttp.ClientResponse) -> tuple[bytes, str]:
    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > config.REFERENCE_IMAGE_MAX_BYTES:
        raise ValueError("Reference image exceeds single file size limit")
    chunks = []
    total = 0
    async for chunk in resp.content.iter_chunked(64 * 1024):
        total += len(chunk)
        if total > config.REFERENCE_IMAGE_MAX_BYTES:
            raise ValueError("Reference image exceeds single file size limit")
        chunks.append(chunk)
    data = b"".join(chunks)
    from PIL import Image   # thư viện biên dịch: nạp trong hàm để lỗi nạp không chặn khởi động gateway
    try:
        with Image.open(BytesIO(data)) as image:
            image.verify()
            fmt = image.format
    except Exception as exc:
        raise ValueError("Reference file is not a valid image") from exc
    if fmt not in _ALLOWED_IMAGE_FORMATS:
        raise ValueError("Reference image only supports JPEG, PNG, WEBP")
    return data, _ALLOWED_IMAGE_FORMATS[fmt]


async def download_one_image(session: aiohttp.ClientSession, url: str, dest: Path) -> Path:
    current = await _validate_url_async(url)
    # Try configured proxy first, fall back to direct connection if blocked
    proxies = []
    if config.PROXY:
        proxies.append(config.PROXY)
    proxies.append(None)
    last_error = None
    for proxy in proxies:
        current = await _validate_url_async(url)
        for _ in range(5):
            try:
                async with session.get(
                    current,
                    allow_redirects=False,
                    proxy=proxy,
                    timeout=aiohttp.ClientTimeout(total=config.REFERENCE_DOWNLOAD_TIMEOUT),
                    headers={"User-Agent": "dola-pool-reference-fetch/1.0"},
                ) as resp:
                    if 300 <= resp.status < 400 and resp.headers.get("Location"):
                        current = await _validate_url_async(urljoin(current, resp.headers["Location"]))
                        continue
                    if resp.status != 200:
                        raise ValueError(f"Failed to download reference image: HTTP {resp.status}")
                    data, suffix = await _read_response_image(resp)
                    path = dest.with_suffix(suffix)
                    path.write_bytes(data)
                    return path
            except Exception as exc:
                last_error = exc
                break
    raise ValueError(str(last_error) if last_error else "Failed to download reference image")


async def download_reference_images(urls: list[str], task_id: str) -> tuple[Path | None, list[str]]:
    """Downloads reference images to temp folder, returns (root_dir, local_paths). Caller must cleanup."""
    urls = await validate_reference_urls(urls)
    if not urls:
        return None, []
    root = Path(tempfile.mkdtemp(prefix=f"dola_ref_{task_id}_"))
    try:
        # resolver kiểm IP tại đúng lần phân giải dùng để nối → bịt DNS rebinding (xem _PublicOnlyResolver).
        connector = aiohttp.TCPConnector(resolver=_PublicOnlyResolver(), use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector) as session:
            paths = []
            for index, url in enumerate(urls):
                paths.append(str(await download_one_image(session, url, root / f"image_{index}")))
        return root, paths
    except Exception:
        for child in root.glob("*"):
            child.unlink(missing_ok=True)
        root.rmdir()
        raise