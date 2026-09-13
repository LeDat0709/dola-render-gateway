# Proxy xoay TMProxy cho nhiều nick

Dola chặn theo **IP** (mã 710022002 "gửi quá dày", 710022004 CAPTCHA). Nhiều nick chung một IP thì tool
phải xếp hàng gửi từng nick và dễ bị chặn cả loạt. Mỗi nick một IP xoay được là cách thoát.

## Cách dùng (không cần lấy tay, không cần dán IP)

1. Mua API key trên tmproxy.com. **Mỗi key = 1 IP tại một thời điểm** → mua số key bằng số nick muốn chạy
   song song (hoặc vài nick chung 1 key, tool xếp hàng trong key đó).
2. Trên tmproxy.com, whitelist **IP máy đang chạy tool** (`ip_allow`), hoặc dùng key có username/password.
3. Trong tool → tab **Proxy → Kho proxy**, dán mỗi dòng một key theo dạng:
   ```
   tmproxy://API_KEY_1
   tmproxy://API_KEY_2
   ```
   rồi bấm **Kiểm** (tool gọi `get-current-proxy`, key hỏng bị loại) và **Chia nick**.
   Hoặc ghi thẳng `tmproxy://API_KEY` vào `accounts/<nick>/proxy.txt`.
4. Trong `.env.local`:
   ```
   DOLA_PROXY_ROTATE_EVERY=2      # đổi IP sau mỗi 2 video của nick (0 = chỉ đổi khi bị chặn)
   DOLA_TMPROXY_LOCATION=0        # id_location theo tài liệu TMProxy, 0 = tự chọn
   DOLA_TMPROXY_ISP=0
   ```
   Khởi động lại gateway.

## Tool tự làm gì

- Lấy IP hiện hành qua `get-current-proxy`, cache tới gần hết hạn (`timeout`), gửi và poll video **cùng IP**.
- Đổi IP bằng `get-new-proxy` khi: tới lượt xoay (`PROXY_ROTATE_EVERY`) **hoặc** Dola báo 710022002 với nick đó.
  Tôn trọng `next_request` của TMProxy (chưa tới giờ thì giữ IP cũ, không lỗi).
- Mỗi key là một **làn giãn nhịp riêng** → nick khác key gửi song song thật, không xếp hàng chung.
- Key lỗi/hết hạn: nick báo lỗi rõ "TMProxy của nick … không lấy được IP", **không** rơi về IP máy.

## Đối chiếu với tool đối thủ (13/09/2026)

Đối thủ đang: lấy proxy **bằng tay** trên web TMProxy → dán vào tool → mỗi lần chạy đổi IP; "tự get, tự chia"
mới là kế hoạch. Tool này sau bản này: tự get, tự chia (Kho proxy), tự đổi theo số video và khi bị chặn.

API tham chiếu (docs.tmproxy.com): `POST https://tmproxy.com/api/proxy/get-new-proxy`
`{"api_key","id_location":0,"id_isp":0}` · `POST …/get-current-proxy` `{"api_key"}` →
`data.{https, socks5, username, password, public_ip, ip_allow, timeout, next_request, expired_at}`.
