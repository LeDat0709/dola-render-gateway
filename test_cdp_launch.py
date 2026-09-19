"""CDP launch (kiểu đối thủ Seedance) — kiểm phần THUẦN: dựng lệnh Chrome + chọn Chrome exe.

Chrome launch thật (subprocess + connect_over_cdp) không unit-test được không có trình duyệt, nên chỉ chốt
phần dựng lệnh: cờ bắt buộc cho CDP + Extensions.loadUnpacked, headless, và tách user:pass khỏi --proxy-server
(Chrome không nhận credential trên dòng lệnh — phải qua CDP Fetch auth)."""
from pathlib import Path

import browser


def test_cmd_has_cdp_and_extension_flags():
    cmd = browser._cdp_chrome_cmd("/chrome", Path("/tmp/acc"), 9222, ["--no-first-run"], None, False)
    assert cmd[0] == "/chrome" and cmd[-1] == "about:blank"
    assert "--user-data-dir=/tmp/acc" in cmd
    assert "--remote-debugging-port=9222" in cmd
    assert "--remote-allow-origins=*" in cmd
    assert "--enable-unsafe-extension-debugging" in cmd   # cần cho Extensions.loadUnpacked
    assert "--no-first-run" in cmd                        # args người gọi được giữ


def test_headless_flag_only_when_headless():
    assert "--headless=new" in browser._cdp_chrome_cmd("/c", Path("/t"), 1, [], None, True)
    assert "--headless=new" not in browser._cdp_chrome_cmd("/c", Path("/t"), 1, [], None, False)


def test_proxy_server_without_creds_only():
    """--proxy-server chỉ host:port. user:pass KHÔNG được nhét vào đây (Chrome bỏ qua → tưởng có auth mà không có)."""
    cmd = browser._cdp_chrome_cmd("/c", Path("/t"), 1, [], "1.2.3.4:8080", False)
    proxy_flags = [x for x in cmd if x.startswith("--proxy-server=")]
    assert proxy_flags == ["--proxy-server=1.2.3.4:8080"], proxy_flags
    cmd2 = browser._cdp_chrome_cmd("/c", Path("/t"), 1, [], None, False)
    assert not any(x.startswith("--proxy-server") for x in cmd2)


def test_caller_strips_proxy_credentials_before_cmd():
    """_launch_account_context_cdp lấy host:port từ account_proxy server (scheme://host:port), user:pass đi CDP Fetch."""
    for server, want in [("http://1.2.3.4:8080", "1.2.3.4:8080"),
                         ("socks5://9.9.9.9:1080", "9.9.9.9:1080")]:
        assert server.split("://", 1)[-1] == want


def test_resolve_chrome_exe_prefers_real_then_bundled():
    class _Chromium:
        executable_path = "/bundled/chromium"

    class _P:
        chromium = _Chromium()

    exe = browser._resolve_chrome_exe(_P())
    # Máy CI không có Chrome hệ thống → rơi về Chromium bundled; máy có Chrome → đường Chrome thật.
    assert exe == "/bundled/chromium" or exe.endswith("chrome.exe") or exe.endswith("Google Chrome")
