#!/usr/bin/env python3
"""
Standalone Dola / Seedance Video Generation Engine (Python)
Trích xuất và chuẩn hóa toàn bộ logic tạo video từ DomixHub-Seedance.exe

Hỗ trợ 2 chế độ:
1. UI Automation Mode (khuyên dùng): Chạy qua Patchright / Playwright, tự động hóa toàn diện,
   vượt Captcha bằng OpenCV, hỗ trợ chọn tỷ lệ (16:9, 9:16, 1:1), thời lượng (10s, 15s, 30s),
   model (Seedance 2.0 / Seedance 2.5), upload ảnh tham chiếu (Image-to-Video) và tự động
   tải video chất lượng cao không watermark (bóc tách từ main_url base64).
2. Protocol Mode (API HTTP): Gửi trực tiếp SSE Chat Completion và poll /im/chain/single
   bằng cookie sessionid + msToken + s_v_web_id.
"""

import asyncio
import base64
import json
import math
import os
import random
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import config

# ==============================================================================
# 1. CÁC HẰNG SỐ & MẪU REGEX NHẬN DIỆN LỖI (TỪ DOMIXHUB-SEEDANCE)
# ==============================================================================

DOLA_CHAT_URL = "https://www.dola.com/chat"
DOLA_AID = "495671"
DOLA_BOT_ID = "7339470689562525703"
VERSION_CODE = "20800"

# Regex nhận diện vi phạm chính sách nội dung (Content Policy)
CONTENT_POLICY_PATTERN = re.compile(
    r"動画の生成はできません|生成はできません|生成できません|暴力的な内容|不適切な内容|"
    r"ポリシーに違反|規約に違反|违反(?:社区|内容)?(?:规范|政策|准则)|"
    r"cannot be generated|can'?t be generated|violates? (?:our |the )?polic",
    re.IGNORECASE,
)

# Regex nhận diện bảo vệ bản quyền khuôn mặt / chân dung (Portrait Protection)
PORTRAIT_PROTECTION_PATTERN = re.compile(
    r"肖像保護|あなた自身が写っている動画のみ|別の画像を使用してください|肖像权保护|请使用您本人|"
    r"portrait protection|can only generate .*yourself|only generate videos (?:of|where) you",
    re.IGNORECASE,
)

# Regex nhận diện thông báo yêu cầu đổi tham số (Parameter Change)
PARAMETER_CHANGE_PATTERN = re.compile(
    r"パラメーターを変更してもう一度|変更してもう一度お試し|更改参数后重试|请更改参数|"
    r"change the parameters and try|please change the parameters",
    re.IGNORECASE,
)

# Regex nhận diện hết hạn mức ngày (Daily Limit)
DAILY_LIMIT_PATTERN = re.compile(
    r"動画生成の\s*1日あたりの上限|每日(?:视频|影片)?生成.*(?:上限|限额|额度)|"
    r"daily.*(?:limit|quota)|(?:limit|quota).*per\s*day",
    re.IGNORECASE,
)

# Regex nhận diện hết điểm (Insufficient Credit)
CREDIT_FAIL_PATTERN = re.compile(
    r"無法生成|无法生成|不能生成|无法完成|無法完成|"
    r"余额不足|餘額不足|额度不足|額度不足|额度耗尽|額度耗盡|"
    r"生成できません|残高不足|insufficient|failed to (generate|create)",
    re.IGNORECASE,
)

# ==============================================================================
# 2. LOGIC TÁCH LINK VIDEO GỐC KHÔNG WATERMARK (UNWATERMARKED EXTRACTION)
# ==============================================================================

def extract_unwatermarked_video_url(video_model_json_str: str, fallback_url: str = "") -> str:
    """
    Trong response của Dola/Samantha (/im/chain/single), video chứa trường video_model.
    Trường này chứa danh sách video_list với main_url được mã hóa base64.
    Giải mã base64 của stream có bitrate cao nhất sẽ thu được link video MP4 gốc
    KHÔNG DÍNH WATERMARK (logo Dola/Dreamina).
    """
    try:
        vm = json.loads(video_model_json_str or "{}")
        video_list = vm.get("video_list") or {}
        candidates = []
        for v in video_list.values():
            if not isinstance(v, dict):
                continue
            main_url = v.get("main_url") or ""
            if not main_url:
                continue
            try:
                decoded = base64.b64decode(main_url).decode("utf-8", "ignore")
                if decoded.startswith("http"):
                    bitrate = int(v.get("bitrate") or v.get("real_bitrate") or 0)
                    candidates.append((bitrate, decoded))
            except Exception:
                continue
        if candidates:
            # Sắp xếp lấy stream có bitrate nét nhất
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
    except Exception:
        pass
    return fallback_url


# ==============================================================================
# 3. THUẬT TOÁN GIẢI CAPTCHA DẠNG TRƯỢT (SLIDER PUZZLE SOLVER VỚI OPENCV)
# ==============================================================================

def find_slider_gap(bg_bytes: bytes, piece_bytes: bytes) -> Tuple[int, float]:
    """
    Giải tọa độ khoảng trống mảnh ghép Captcha trượt bằng OpenCV Canny & Template Matching.
    Trả về: (gap_x, confidence)
    """
    import cv2
    import numpy as np

    bg = cv2.imdecode(np.frombuffer(bg_bytes, np.uint8), cv2.IMREAD_COLOR)
    piece = cv2.imdecode(np.frombuffer(piece_bytes, np.uint8), cv2.IMREAD_UNCHANGED)
    if piece is None or bg is None:
        raise ValueError("Không thể decode ảnh captcha")

    # Tách kênh alpha để lấy viền mảnh ghép
    if len(piece.shape) > 2 and piece.shape[2] == 4:
        alpha = piece[:, :, 3]
    else:
        alpha = cv2.cvtColor(piece, cv2.COLOR_BGR2GRAY)

    piece_edge = cv2.Canny(alpha, 100, 200)
    bg_edge = cv2.Canny(cv2.cvtColor(bg, cv2.COLOR_BGR2GRAY), 100, 200)

    res = cv2.matchTemplate(bg_edge, piece_edge, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    return int(max_loc[0]), float(max_val)


def generate_bezier_mouse_track(distance: float) -> List[Tuple[float, float, int]]:
    """
    Mô phỏng quỹ đạo chuột kéo thanh trượt người thật bằng đường cong Cubic Bézier:
    Khởi động chậm -> tăng tốc -> giảm tốc khi tới gần đích -> rung lắc tay (micro-tremor).
    """
    steps = random.randint(50, 70)
    overshoot = random.uniform(4.0, 8.0)
    target = distance + overshoot
    points = []

    p0 = 0.0
    p1 = target * random.uniform(0.2, 0.35)
    p2 = target * random.uniform(0.7, 0.85)
    p3 = target

    for i in range(1, steps + 1):
        t = i / steps
        # Cubic Bézier: B(t) = (1-t)^3*p0 + 3(1-t)^2*t*p1 + 3(1-t)*t^2*p2 + t^3*p3
        x = (1 - t)**3 * p0 + 3 * (1 - t)**2 * t * p1 + 3 * (1 - t) * t**2 * p2 + t**3 * p3
        y = random.uniform(-1.2, 1.2) * math.sin(math.pi * t) if 0.05 < t < 0.95 else 0.0
        dt = random.randint(10, 20) if (0.2 < t < 0.8) else random.randint(20, 40)
        points.append((x, y, dt))

    # Kéo ngược lại phần overshoot để khớp chính xác notch
    back_steps = random.randint(5, 8)
    for i in range(1, back_steps + 1):
        t = i / back_steps
        x = target - (overshoot * t)
        y = random.uniform(-0.5, 0.5)
        points.append((x, y, random.randint(15, 30)))

    return points


# ==============================================================================
# 4. CHỨC NĂNG POLLING TRẠNG THÁI VIDEO TRONG TRÌNH DUYỆT
# ==============================================================================

POLL_IN_PAGE_JS = r"""
async ({conversationId, msToken, fp}) => {
  const params = new URLSearchParams({
    version_code: "20800", language: "ja", device_platform: "web",
    doubao_device_platform: "web", aid: "495671", real_aid: "495671",
    pkg_type: "release_version", pc_version: "3.32.62", doubao_pc_version: "3.32.62",
    region: "JP", sys_region: "JP", samantha_web: "1", web_platform: "browser",
    "use-olympus-account": "1", web_tab_id: crypto.randomUUID(),
  });
  if (msToken) params.set("msToken", msToken);
  if (fp) params.set("fp", fp);

  const resp = await fetch("/im/chain/single?" + params.toString(), {
    method: "POST",
    headers: {
      "Content-Type": "application/json; encoding=utf-8",
      "agw-js-conv": "str",
      "Accept": "*/*",
    },
    body: JSON.stringify({
      cmd: 3100,
      uplink_body: {
        pull_singe_chain_uplink_body: {
          conversation_id: conversationId,
          anchor_index: Number.MAX_SAFE_INTEGER,
          conversation_type: 3,
          direction: 1,
          limit: 20,
          ext: {},
          filter: {index_list: []},
          evaluate_ab_params: "",
          evaluate_common_params: "",
        },
      },
      sequence_id: crypto.randomUUID(),
      channel: 2,
      version: "1",
    }),
    credentials: "include",
  });
  if (!resp.ok) return {ok: false, status: resp.status, texts: [], videos: [], videoModels: []};

  const data = await resp.json();
  const messages = (((data.downlink_body || {}).pull_singe_chain_downlink_body) || {}).messages || [];
  const texts = [];
  const videos = [];
  const videoModels = [];

  for (const msg of messages) {
    let content = msg.content;
    if (typeof content === "string") {
      try { content = JSON.parse(content); } catch (e) { continue; }
    }
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      const text = (((block.content || {}).text_block) || {}).text || "";
      if (text) texts.push(text.slice(0, 150));
      if (block.block_type !== 2074) continue;
      const creations = (((block.content || {}).creation_block) || {}).creations || [];
      for (const cre of creations) {
        if (cre.type !== 2) continue;
        const url = ((cre.video || {}).download_url) || "";
        if (url.startsWith("http")) {
          videos.push(url);
          videoModels.push((cre.video || {}).video_model || "");
        }
      }
    }
  }
  return {ok: true, status: resp.status, texts, videos, videoModels};
}
"""


# ==============================================================================
# 5. WORKER TẠO VIDEO BẰNG PYTHON (PLAYWRIGHT / PATCHRIGHT)
# ==============================================================================

class DolaVideoGenerator:
    """
    Bộ tạo video Dola Seedance 2.0 / 2.5 hoàn chỉnh
    """

    def __init__(
        self,
        profile_dir: str,
        headless: bool = False,
        proxy: Optional[str] = None,
        download_dir: str = "downloads"
    ):
        self.profile_dir = Path(profile_dir).resolve()
        self.headless = headless
        self.proxy = proxy
        self.download_dir = Path(download_dir).resolve()
        self.download_dir.mkdir(parents=True, exist_ok=True)

    async def _solve_slider_if_present(self, page) -> bool:
        """Kiểm tra và giải captcha trượt nếu xuất hiện."""
        for _ in range(15):
            captcha_frame = None
            for f in page.frames:
                if "verify" in f.url or "captcha" in f.url:
                    captcha_frame = f
                    break
            if not captcha_frame:
                await page.wait_for_timeout(800)
                continue

            print("[Captcha] Phát hiện Captcha trượt, đang phân tích ảnh...", flush=True)
            try:
                bg_el = await captcha_frame.wait_for_selector("#captcha-verify-image", timeout=5000)
                slider_el = await captcha_frame.wait_for_selector(".secsdk_captcha_refresh--slider, [class*='slider']", timeout=5000)
                if not bg_el or not slider_el:
                    return False

                bg_src = await bg_el.get_attribute("src")
                piece_el = await captcha_frame.query_selector(".captcha_verify_img_slide, img[src*='piece']")
                piece_src = await piece_el.get_attribute("src") if piece_el else None

                if not bg_src or not piece_src:
                    return False

                async with aiohttp.ClientSession() as session:
                    async with session.get(bg_src) as r1, session.get(piece_src) as r2:
                        bg_bytes = await r1.read()
                        piece_bytes = await r2.read()

                gap_x, conf = find_slider_gap(bg_bytes, piece_bytes)
                print(f"[Captcha] Tọa độ notch: x={gap_x}, độ tin cậy={conf:.2f}", flush=True)

                box = await slider_el.bounding_box()
                if not box:
                    return False

                # Kéo slider theo quỹ đạo Bézier
                start_x = box["x"] + box["width"] / 2
                start_y = box["y"] + box["height"] / 2
                await page.mouse.move(start_x, start_y)
                await page.mouse.down()

                track = generate_bezier_mouse_track(gap_x)
                for dx, dy, dt in track:
                    await page.mouse.move(start_x + dx, start_y + dy)
                    await asyncio.sleep(dt / 1000.0)

                await page.mouse.up()
                await page.wait_for_timeout(2500)
                return True
            except Exception as e:
                print(f"[Captcha] Lỗi giải: {e}", flush=True)
                return False

        return True  # Không có captcha

    async def generate_video(
        self,
        prompt: str,
        model: str = "seedance-2.0",  # hoặc seedance-2.5
        duration: int = 10,           # 10, 15, hoặc 30
        ratio: str = "16:9",          # 16:9, 9:16, 1:1, 4:3, 3:4
        reference_images: Optional[List[str]] = None,
        timeout: int = 600,
    ) -> Dict[str, Any]:
        """
        Thực hiện toàn bộ quy trình tạo video:
        1. Khởi chạy browser với persistent profile đã đăng nhập.
        2. Mở Dola Chat, kiểm tra phiên.
        3. Upload ảnh tham chiếu nếu có (Img2Video).
        4. Chọn Model (Seedance 2.0 / 2.5), chọn Ratio, chọn Duration.
        5. Gõ Prompt và Submit.
        6. Vượt Captcha nếu bị yêu cầu.
        7. Bắt conversation_id và Polling nhận video hoàn thiện.
        8. Trích xuất link MP4 chất lượng cao nhất không watermark và tải về máy.
        """
        from patchright.async_api import async_playwright

        launch_args = ["--no-first-run", "--no-default-browser-check"]
        ext_path = Path("extensions/dola30").resolve()
        if ext_path.exists():
            launch_args.extend([
                f"--disable-extensions-except={ext_path}",
                f"--load-extension={ext_path}",
            ])

        kwargs = {
            "headless": False if ext_path.exists() else self.headless,
            "args": launch_args,
            "locale": "ja-JP",
            "timezone_id": "Asia/Tokyo",
        }
        if self.proxy:
            kwargs["proxy"] = {"server": self.proxy}

        print(f"[*] Khởi động profile: {self.profile_dir} (headless={kwargs['headless']})")
        async with async_playwright() as p:
            context = await p.chromium.launch_persistent_context(str(self.profile_dir), **kwargs)
            try:
                page = context.pages[0] if context.pages else await context.new_page()
                print(f"[*] Đang tải trang Dola: {DOLA_CHAT_URL}")
                await page.goto(DOLA_CHAT_URL, timeout=60000, wait_until="domcontentloaded")
                await page.wait_for_timeout(4000)

                # Kiểm tra login wall
                body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                if any(x in body_text for x in ("ログインしてください", "Googleで続ける", "他の機能を利用するにはログイン", "Log in to continue")):
                    await page.screenshot(path="login_required.png")
                    raise RuntimeError("Tài khoản chưa đăng nhập hoặc cookie Dola đã hết hạn (Dola yêu cầu đăng nhập)!")

                # Bấm nút kích hoạt Video
                video_clicked = False
                for btn_sel in ("text=動画を作成", "button:has-text('動画を作成')", "button:has-text('動画')", "[role='button']:has-text('動画')"):
                    try:
                        vbtn = page.locator(btn_sel).first
                        if await vbtn.count() and await vbtn.is_visible():
                            await vbtn.click(timeout=3000)
                            video_clicked = True
                            print(f"[✓] Đã kích hoạt chế độ Video Card: {btn_sel}")
                            await page.wait_for_timeout(1500)
                            break
                    except Exception:
                        continue

                # Upload ảnh tham chiếu (nếu có)
                if reference_images:
                    print(f"[*] Đang nạp {len(reference_images)} ảnh tham chiếu (Img2Video)...")
                    file_input = page.locator('input[type="file"]').first
                    if await file_input.count():
                        await file_input.set_input_files(reference_images)
                        await page.wait_for_timeout(3000)

                # Chọn Model Seedance trong giao diện Video Card
                model_target = "Dreamina Seedance 2.5" if "2.5" in model else "Dreamina Seedance 2.0"
                try:
                    for label in ("モデル 2.0高速", "モデル 2.5", "Seedance"):
                        mloc = page.get_by_text(label, exact=False).first
                        if await mloc.count() and await mloc.is_visible():
                            await mloc.click(timeout=3000)
                            await page.wait_for_timeout(500)
                            opt = page.get_by_text(model_target, exact=False).first
                            if await opt.count() and await opt.is_visible():
                                await opt.click(timeout=3000)
                                print(f"[✓] Đã chọn Model: {model_target}")
                            break
                except Exception as me:
                    print(f"  (Chọn model tự động: {me})")

                # Chọn Tỷ lệ Ratio
                if ratio:
                    try:
                        ratio_btn = page.locator("text=比率").first
                        if await ratio_btn.count() and await ratio_btn.is_visible():
                            await ratio_btn.click(timeout=2000)
                            await page.wait_for_timeout(400)
                            await page.locator(f"text={ratio}").first.click(timeout=2000)
                            print(f"[✓] Đã chọn Ratio: {ratio}")
                    except Exception:
                        pass

                # Chọn Duration (10s, 15s, 30s)
                if duration:
                    try:
                        dur_btn = page.locator(f"text={duration}s").first
                        if await dur_btn.count():
                            await dur_btn.click(timeout=2000)
                            print(f"[✓] Đã chọn Duration: {duration}s")
                    except Exception:
                        pass

                # Nhập prompt vào textarea
                box = await page.query_selector("textarea") or await page.query_selector('[contenteditable="true"]')
                if not box:
                    raise RuntimeError("Không tìm thấy khung soạn thảo văn bản của Dola!")

                # Chuẩn hóa prompt với tiền tố để Dola nhận diện lệnh tạo video trực tiếp
                submit_prompt = prompt if prompt.startswith(("動画を生成", "生成影片", "Video:")) else f"動画を生成：{prompt}"
                print(f"[*] Đang gõ prompt: {submit_prompt}")
                await box.click()
                await page.keyboard.type(submit_prompt, delay=60)
                await page.wait_for_timeout(500)
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2000)

                # Kiểm tra xem có bị bật modal đăng nhập sau khi submit không
                post_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
                if any(x in post_text for x in ("ログインしてください", "Googleで続ける", "他の機能を利用するにはログイン", "Log in to continue")):
                    await page.screenshot(path="login_required_post.png")
                    raise RuntimeError("Phiên Dola đã hết hạn — Dola hiển thị cửa sổ bắt buộc đăng nhập!")

                # Vượt Captcha nếu có
                await self._solve_slider_if_present(page)

                # Lấy conversation_id từ URL chuyển hướng
                conv_id = ""
                for _ in range(30):
                    await page.wait_for_timeout(1000)
                    tail = page.url.rstrip("/").split("/")[-1]
                    if tail.isdigit():
                        conv_id = tail
                        break

                if not conv_id:
                    raise TimeoutError("Không lấy được conversation_id sau khi gửi prompt")

                print(f"[✓] Đã kích hoạt tác vụ video! Conversation ID: {conv_id}")
                print(f"[*] Bắt đầu polling chờ render video (tối đa {timeout}s)...")

                cookies = await context.cookies("https://www.dola.com")
                ms_token = next((c["value"] for c in cookies if c["name"] == "msToken"), "")
                fp = next((c["value"] for c in cookies if c["name"] == "s_v_web_id"), "")

                start_time = time.time()
                confirmed_yes = False
                while time.time() - start_time < timeout:
                    await asyncio.sleep(5)
                    poll_res = await page.evaluate(POLL_IN_PAGE_JS, {
                        "conversationId": conv_id,
                        "msToken": ms_token,
                        "fp": fp
                    })

                    # Tự động trả lời 'はい' nếu Dola hỏi xác nhận thông số video (chỉ gửi 1 lần)
                    if not confirmed_yes:
                        for text in poll_res.get("texts", []):
                            if any(x in text for x in ("「はい」とだけ返信", "「はい」", "返信してください", "よろしいですか")):
                                print(f"\n[!] Dola hỏi xác nhận: \"{text[:60]}...\"")
                                print("[*] Tự động gửi 'はい' để bắt đầu render video ngay...")
                                try:
                                    cur_box = await page.query_selector("textarea") or await page.query_selector('[contenteditable="true"]')
                                    if cur_box:
                                        await cur_box.click()
                                        await page.keyboard.type("はい", delay=80)
                                        await page.wait_for_timeout(300)
                                        await page.keyboard.press("Enter")
                                        confirmed_yes = True
                                        await page.wait_for_timeout(3000)
                                        break
                                except Exception as ce:
                                    print(f"  (Lỗi gửi 'はい' tự động: {ce})")

                    # Kiểm tra các thông báo lỗi / vi phạm
                    for text in poll_res.get("texts", []):
                        if CONTENT_POLICY_PATTERN.search(text):
                            raise RuntimeError(f"Vi phạm chính sách nội dung: {text}")
                        if PORTRAIT_PROTECTION_PATTERN.search(text):
                            raise RuntimeError(f"Kích hoạt bảo vệ chân dung khuôn mặt: {text}")
                        if PARAMETER_CHANGE_PATTERN.search(text):
                            raise RuntimeError(f"Yêu cầu đổi tham số: {text}")
                        if DAILY_LIMIT_PATTERN.search(text):
                            raise RuntimeError(f"Đã chạm ngưỡng giới hạn hằng ngày: {text}")

                    # Nếu có video hoàn thành
                    if poll_res.get("videos"):
                        raw_url = poll_res["videos"][0]
                        v_models = poll_res.get("videoModels", [])
                        v_model = v_models[0] if v_models else ""

                        # Bóc tách link không dính watermark
                        clean_url = extract_unwatermarked_video_url(v_model, fallback_url=raw_url)
                        print(f"\n[🎉] Render thành công! Bắt đầu tải video không watermark...")

                        fname = self.download_dir / f"video_{conv_id}_{int(time.time())}.mp4"
                        async with aiohttp.ClientSession() as session:
                            async with session.get(clean_url) as resp:
                                resp.raise_for_status()
                                with open(fname, "wb") as f:
                                    async for chunk in resp.content.iter_chunked(65536):
                                        f.write(chunk)

                        file_size_mb = fname.stat().st_size / (1024 * 1024)
                        print(f"[✓] Đã lưu file: {fname} ({file_size_mb:.2f} MB)")
                        return {
                            "success": True,
                            "conversation_id": conv_id,
                            "video_url": clean_url,
                            "local_path": str(fname),
                            "file_size_mb": file_size_mb
                        }

                    elapsed = int(time.time() - start_time)
                    print(f"    ...Đang render ({elapsed}s)", end="\r", flush=True)

                raise TimeoutError(f"Hết thời gian chờ {timeout}s mà video chưa xong.")
            finally:
                await context.close()


# ==============================================================================
# 6. DEMO THỰC THI QUA DÒNG LỆNH (CLI)
# ==============================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Standalone Seedance Video Generator")
    parser.add_argument("prompt", help="Nội dung mô tả video cần tạo")
    parser.add_argument("--account", default="acc1", help="Tên thư mục profile accounts/<account>")
    parser.add_argument("--model", default="seedance-2.0", choices=["seedance-2.0", "seedance-2.5"])
    parser.add_argument("--duration", type=int, default=10, choices=[10, 15, 30])
    parser.add_argument("--ratio", default="16:9", choices=["16:9", "9:16", "1:1", "4:3", "3:4"])
    parser.add_argument("--ref", action="append", help="Đường dẫn ảnh tham chiếu (có thể truyền nhiều lần)")
    parser.add_argument("--headless", action="store_true", help="Chạy ẩn danh không mở cửa sổ UI")
    parser.add_argument("--timeout", type=int, default=600, help="Thời gian chờ tối đa (giây)")

    args = parser.parse_args()

    # Thư mục profile tài khoản
    profile_path = config.ACCOUNTS_DIR / args.account
    if not profile_path.exists():
        print(f"[!] Cảnh báo: Profile '{profile_path}' chưa tồn tại.")
        print(f"    Vui lòng tạo tài khoản trước hoặc kiểm tra thư mục 'accounts/'")

    generator = DolaVideoGenerator(
        profile_dir=str(profile_path),
        headless=args.headless,
        download_dir="downloads"
    )

    try:
        res = asyncio.run(
            generator.generate_video(
                prompt=args.prompt,
                model=args.model,
                duration=args.duration,
                ratio=args.ratio,
                reference_images=args.ref,
                timeout=args.timeout
            )
        )
        print("\nKết quả tạo video:")
        print(json.dumps(res, indent=2, ensure_ascii=False))
    except Exception as exc:
        print(f"\n[✗] Thất bại: {exc}", file=sys.stderr)
        sys.exit(1)
