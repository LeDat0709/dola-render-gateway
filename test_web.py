#!/usr/bin/env python3
import urllib.request
import ssl

ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

urls = [
    "http://teamminh.limcaring.com",
    "https://teamminh.limcaring.com"
]

for u in urls:
    try:
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5, context=ctx) as res:
            print(f"[+] Kết nối {u} THÀNH CÔNG! Mã phản hồi: {res.status}")
    except Exception as e:
        print(f"[-] Kết nối {u} thất bại: {e}")
