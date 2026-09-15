"""Video worker helpers: /im/chain/single polling, URL extraction, download."""
import asyncio
import base64
import json
import re
import time
from pathlib import Path

import aiohttp

import config

# Poll /im/chain/single for video status
POLL_JS = r"""
async ({conversationId, msToken, fp}) => {
  // Current protocol: uplink_body.pull_singe_chain_uplink_body
  const params = new URLSearchParams({
    version_code: "20800", language: "ja", device_platform: "web",
    doubao_device_platform: "web", aid: "495671", real_aid: "495671",
    pkg_type: "release_version", pc_version: "3.32.61", doubao_pc_version: "3.32.61",
    region: "JP", sys_region: "JP", samantha_web: "1", web_platform: "browser",
    "use-olympus-account": "1", web_tab_id: crypto.randomUUID(),
  });

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
  if (!resp.ok) return {ok: false, status: resp.status, texts: [], videos: []};

  const data = await resp.json();
  const messages =
    (((data.downlink_body || {}).pull_singe_chain_downlink_body) || {}).messages || [];
  const texts = [];
  const videos = [];
  const videoModels = [];
  let images = 0;   // type-1 creations => Dola rendered an IMAGE, not a video
  for (const msg of messages) {
    let content = msg.content;
    if (typeof content === "string") {
      try { content = JSON.parse(content); } catch (e) { continue; }
    }
    if (!Array.isArray(content)) continue;
    for (const block of content) {
      const text = (((block.content || {}).text_block) || {}).text || "";
      if (text) texts.push(text.slice(0, 600));  // full confirm/refusal wording
      if (block.block_type !== 2074) continue;
      const creations = (((block.content || {}).creation_block) || {}).creations || [];
      for (const cre of creations) {
        if (cre.type === 1 && cre.image) { images += 1; continue; }
        if (cre.type !== 2) continue;
        const url = ((cre.video || {}).download_url) || "";
        if (url.startsWith("http")) {
          videos.push(url);
          videoModels.push((cre.video || {}).video_model || "");
        }
      }
    }
  }
  return {ok: true, status: resp.status, texts, videos, videoModels, images};
}
"""


# /chat/completion exactly as the Dola web UI sends it (captured 2026-09-09, pc_version 3.36.0).
# MUST run in the page's main world (isolated_context=False): the bdms SDK hooks window.fetch
# there and appends the msToken + a_bogus signature; unsigned calls get 710010202.
SUBMIT_JS = r"""
async ({prompt, ratio, duration, model, query, ackIdleMs}) => {
  const nowMs = Date.now();
  const nowSec = Math.floor(nowMs / 1000);
  const uuid = () => crypto.randomUUID();
  const localConv = "local_" + String(nowMs) + String(Math.floor(Math.random() * 1000)).padStart(3, "0");
  const replyFormat = "生成された動画：%s";
  const abilityParam = {model, duration: Number(duration),
    input_box_content: {user_input_content: prompt, reply_message_format: replyFormat}};
  if (ratio) abilityParam.ratio = ratio;
  // Khan 30s (đo thực 13/09 từ extension "Dola 30 Sec By KHAN"): với model 2.5, khai cost/queue giống
  // extension → Dola tính 30s là 2 credit thay vì 4 và nhận lệnh cho nick free. Đồng thời KHÔNG nhét
  // "30秒" vào text tin nhắn (extension gửi prompt thuần + tỷ lệ), nếu không trợ lý Dola đọc thấy "30秒"
  // rồi từ chối/hỏi lại — đây là lý do đường cũ hay kẹt vòng hỏi-đáp.
  const khanMode = /2\.5/.test(String(model));
  const khanExtra = khanMode ? {allow_free_queue: true, accept_queue: true, credits: 1, cost: 1,
    audio: false, generate_audio: false, with_audio: false, has_audio: false, bgm: false, sound: false,
    no_watermark: true, remove_logo: true, quality: "max"} : {};
  Object.assign(abilityParam, khanExtra);
  const initOpt = Object.assign({need_ack_conversation: true}, khanExtra);
  // Ép chế độ trò chuyện của Dola tôn trọng specs: nhét chỉ thị vào TEXT tin nhắn (không vào prompt video).
  const _orient = {"9:16":"縦","3:4":"縦","16:9":"横","4:3":"横","1:1":"正方形"}[ratio] || "";
  const _spec = (Number(duration) ? Number(duration)+"秒" : "") + (ratio ? "・アスペクト比"+ratio+(_orient?"（"+_orient+"）":"") : "");
  const _directive = (_spec && !khanMode) ? ("【この仕様で直接生成してください（"+_spec+"）。長さ・比率は変更せず、追加の確認は不要です】\n") : "";
  const _khanText = replyFormat.replace("%s", prompt + (ratio ? "、" + ratio : ""));

  const body = {
    client_meta: {
      local_conversation_id: localConv, conversation_id: "", bot_id: "7339470689562525703",
      last_section_id: "", last_message_index: null,
      local_permissions: [
        {permission_name: "ACCESS_COARSE_LOCATION", status: 3},
        {permission_name: "ACCESS_FINE_LOCATION", status: 3},
        {permission_name: "ACCESS_BACKGROUND_LOCATION", status: 3},
      ],
    },
    messages: [{
      local_message_id: uuid(),
      content_block: [{
        block_type: 10000,
        content: {text_block: {text: khanMode ? _khanText : (_directive + prompt), icon_url: "", icon_url_dark: "", summary: ""},
                  pc_event_block: ""},
        block_id: uuid(), parent_id: "", meta_info: [], append_fields: [],
      }],
      message_status: 0,
    }],
    option: {
      send_message_scene: "", create_time_ms: nowMs, collect_id: "", is_audio: false,
      answer_with_suggest: false, tts_switch: false, need_deep_think: 0, click_clear_context: false,
      from_suggest: false, is_regen: false, is_replace: false, is_from_click_option: false,
      is_from_click_softlink: false, disable_sse_cache: false, select_text_action: "",
      is_select_text: false, resend_for_regen: false, scene_type: 0, unique_key: uuid(), start_seq: 0,
      need_create_conversation: true, conversation_init_option: initOpt,
      regen_query_id: [], edit_query_id: [], regen_instruction: "", no_replace_for_regen: false,
      message_from: 0, shared_app_name: "", shared_app_id: "",
      sse_recv_event_options: {support_chunk_delta: true}, is_ai_playground: false, is_old_user: false,
      // Khan/UI thật gửi is_recovery:false; true có thể bị server coi là gửi lại tin chat cũ. Giữ true ngoài khanMode.
      recovery_option: {is_recovery: !khanMode, req_create_time_sec: nowSec, append_sse_event_scene: 0},
      message_storage_type: 0, related_deleted_message_ids: {}, connector_info_list: [],
      model_config: {model_item_key: "", model_extra_params: {}},
      aggregate_params: {conversation_mode: "", mode_id: "", model_item_key: "", agent_mode: "",
                         reasoning_effort: "", provider_id: ""},
    },
    // Khan rải extras cả lên chat_ability (anh em của ability_type) — server đọc credit/queue ở đây.
    chat_ability: Object.assign({ability_type: 17, ability_param: JSON.stringify(abilityParam)}, khanExtra),
    user_context: [],
    ext: {answer_with_suggest: "0", sub_conv_firstmet_type: "1", collection_id: "", is_finish: "1",
          conversation_init_option: JSON.stringify(initOpt), commerce_credit_config_enable: "0"},
  };
  Object.assign(body, khanExtra);   // Khan cũng đặt extras ở root body (diff toàn thân 13/09: 13 khoá)

  const params = new URLSearchParams(query);
  params.set("web_tab_id", uuid());
  const resp = await fetch("/chat/completion?" + params.toString(), {
    method: "POST",
    headers: {"Content-Type": "application/json", "agw-js-conv": "str, str", "Accept": "*/*",
              "last-event-id": "undefined"},
    body: JSON.stringify(body),
    credentials: "include",
    referrer: location.origin + "/chat/" + localConv,
  });
  if (!resp.ok || !resp.body) {
    return {status: resp.status, convId: "", events: [], errors: [(await resp.text()).slice(0, 600)]};
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const events = [];
  const errors = [];
  let convId = "";
  let ackAt = 0;
  while (true) {
    // Once the ack is in, the rest of the stream is the assistant's text; don't sit on it.
    const chunk = await Promise.race([
      reader.read(),
      new Promise(r => setTimeout(() => r({idle: true}), ackAt ? ackIdleMs : 120000)),
    ]);
    if (chunk.idle) { try { await reader.cancel(); } catch (e) {} break; }
    if (chunk.done) break;
    buffer += decoder.decode(chunk.value, {stream: true});
    let idx;
    while ((idx = buffer.indexOf("\n\n")) >= 0) {
      const rawEvent = buffer.slice(0, idx);
      buffer = buffer.slice(idx + 2);
      let eventName = "";
      const dataLines = [];
      for (const line of rawEvent.split("\n")) {
        if (line.startsWith("event:")) eventName = line.slice(6).trim();
        else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
      }
      const dataStr = dataLines.join("\n");
      events.push({event: eventName, data: dataStr.slice(0, 300)});
      // error_code:0 is the SUCCESS envelope Dola stamps on normal events — only a
      // NON-zero code is a real error. Misreading 0 as an error caused double submits.
      const ec = dataStr.match(/"error_code"\s*:\s*(\d+)/);
      if (ec && ec[1] !== "0") errors.push(dataStr.slice(0, 1000));
      if (eventName === "SSE_ACK") {
        try { convId = (JSON.parse(dataStr).ack_client_meta || {}).conversation_id || ""; } catch (e) {}
        if (convId) ackAt = Date.now();
      }
    }
  }
  return {status: resp.status, convId, events: events.slice(0, 8), errors};
}
"""

def extract_unwatermarked_url(video_model_str: str, fallback_url: str) -> str:
    """Extracts unwatermarked video URL (base64) from video_model.video_list."""
    try:
        vm = json.loads(video_model_str or "{}")
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
            except Exception:
                continue
            if decoded.startswith("http"):
                candidates.append((int(v.get("bitrate") or v.get("real_bitrate") or 0), decoded))
        if candidates:
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]
    except Exception:
        pass
    return fallback_url


class RiskControlError(Exception):
    """Risk control triggered (slide / rate limit)."""


class RateLimitedError(RiskControlError):
    """Dola 710022002 "gửi quá dày": chặn ngắn hạn theo IP/tài khoản — KHÔNG phải hết lượt ngày, không
    phải lỗi của nick. Nhiều nick chung một IP (không proxy) thì dính cả loạt, nên xoay nick ngay chỉ làm
    Dola chặn mạnh hơn; browser_pool tạm dừng gửi toàn bộ một lúc rồi mới tiếp."""


def _dola_msg(err: str) -> str:
    """Lấy câu Dola nói ra khỏi JSON lỗi; không phải JSON thì trả nguyên (cắt ngắn)."""
    try:
        d = json.loads(err)
        return str(d.get("error_msg") or d.get("message") or d.get("error") or err)[:200]
    except (ValueError, AttributeError):
        return err[:200]


class SubmitDelivered(Exception):
    """The /chat/completion POST reached Dola (HTTP 200) but no conversation_id was parsed.

    Dola may have accepted and charged the job, so the caller MUST NOT re-submit (that would
    double-charge); it should recover the conversation_id by polling recent conversations.
    """


class SubmitRejected(Exception):
    """Dola answered /chat/completion with a non-2xx status and no risk-control code.

    Nothing was accepted or charged, and it is not the account's fault (WAF/proxy/5xx).
    Before this class every such reply was RiskControlError → 30-minute cooldown, so one
    bad proxy or a Dola outage benched EVERY nick with 0 videos used.
    """


def _check_submit(result: dict) -> str:
    """Returns conversation_id, or raises. A parsed convId wins over any incidental event."""
    conv_id = result.get("convId") or ""
    if conv_id:
        return conv_id  # accepted — ignore error_code:0 / patch events in the same stream

    for err in result.get("errors", []):
        if "710022004" in err or "slide" in err or "shark" in err:
            raise RiskControlError(f"Captcha risk control triggered: {err[:300]}")
        if "710022002" in err:
            raise RateLimitedError(f"Dola tạm chặn vì gửi quá dày (710022002): {_dola_msg(err)}")

    status = result.get("status")
    if status and 200 <= status < 300:
        # Delivered but no convId in the stream — do NOT re-submit; recover instead.
        raise SubmitDelivered(json.dumps(result.get("events", []), ensure_ascii=False)[:300])
    body = "; ".join(result.get("errors") or []) or json.dumps(result.get("events", []), ensure_ascii=False)
    raise SubmitRejected(f"HTTP {status}: {body[:200]}")


DOWNLOAD_RETRIES = 3
DOWNLOAD_RETRY_SEC = 5


class DownloadError(RuntimeError):
    """Video đã dựng xong trên Dola (đã trừ credit) nhưng không tải về được; .url để tải tay."""

    def __init__(self, url: str, last: str):
        super().__init__(f"Không tải được video sau {DOWNLOAD_RETRIES} lần ({last}) — tải tay: {url}")
        self.url = url


async def _fetch_to_file(url: str, fname: Path, proxy: str | None = None):
    timeout = aiohttp.ClientTimeout(total=300)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url, proxy=proxy or config.PROXY or None) as resp:
            resp.raise_for_status()
            with open(fname, "wb") as f:
                async for chunk in resp.content.iter_chunked(1 << 16):
                    f.write(chunk)
    size = fname.stat().st_size
    if size < 100_000:   # video thật luôn > 100KB; nhỏ hơn = tải lỗi/rỗng
        raise RuntimeError(f"Video tải về bị lỗi/rỗng ({size/1024:.0f}KB)")


def _prompt_slug(prompt: str, n: int = 30) -> str:
    """Mấy chữ ĐẦU của prompt cho tên file: dòng đầu, bỏ ký tự cấm, gộp khoảng trắng, cắt n ký tự.
    Giữ Unicode (tiếng Việt/Trung) vì Mac/Windows đọc được; rỗng nếu prompt rỗng."""
    lines = (prompt or "").strip().splitlines()
    s = lines[0] if lines else ""
    s = re.sub(r'[\\/:*?"<>|]+', " ", s)          # ký tự cấm trong tên file
    s = re.sub(r"\s+", " ", s).strip()[:n].strip()
    return s.replace(" ", "_")


async def _download(url: str, account: str, prompt: str = "") -> Path:
    """Downloads video to DOWNLOAD_DIR (tên: <chữ đầu prompt>_<nick>_<thời gian>.mp4) and returns local path.

    Render mất 2–12 phút và đã trừ credit; một lần rớt mạng lúc tải không được làm mất job →
    thử DOWNLOAD_RETRIES lần, hết thì ném DownloadError mang URL để người dùng tải tay.
    """
    from browser import account_proxy_url
    proxy = account_proxy_url(account) or None      # tải video đi đúng proxy của nick, không phải IP chung
    dl_dir = Path(config.DOWNLOAD_DIR)
    dl_dir.mkdir(parents=True, exist_ok=True)
    parts = [_prompt_slug(prompt), account, time.strftime('%Y%m%d_%H%M%S')]   # theo PROMPT, không STT
    fname = dl_dir / ("_".join(p for p in parts if p) + ".mp4")   # bỏ phần rỗng, khỏi "__"
    # Tải qua PROXY của nick trước; rớt hết thì thử ĐI THẲNG (bỏ proxy) — CDN video thường không cần IP nick,
    # nên proxy chậm/chết không làm mất video (trước đây rớt proxy là mất luôn bản về máy). last giữ lỗi cuối.
    lanes = [proxy] if not proxy else [proxy, None]
    last, saved = "", False
    for lane in lanes:
        for attempt in range(1, DOWNLOAD_RETRIES + 1):
            try:
                await _fetch_to_file(url, fname, proxy=lane)
                if lane is None and proxy:
                    print(f"[{account}] ✓ tải TRỰC TIẾP (bỏ proxy) OK", flush=True)
                saved = True
                break
            except Exception as e:
                last = str(e)[:120]
                fname.unlink(missing_ok=True)
                print(f"[{account}] tải video ({'proxy' if lane else 'trực tiếp'}) lỗi {attempt}/{DOWNLOAD_RETRIES}: {last}", flush=True)
                # Proxy CẮT NGẮN tải (ContentLengthError/"not completed") là lỗi cố định của proxy đó, không phải
                # rớt mạng tạm thời → retry 3× đều fail rồi mới đi thẳng (phí ~15s/video). Sang lane sau NGAY.
                low = last.lower()
                if lane is not None and ("not completed" in low or "content length" in low or "not enough data" in low):
                    break
                await asyncio.sleep(DOWNLOAD_RETRY_SEC)
        if saved:
            break
    if not saved:
        raise DownloadError(url, last)
    print(f"[{account}] ✓ Đã lưu video: {fname}", flush=True)   # in rõ ĐƯỜNG DẪN để biết video nằm đâu
    # Xoá logo do _strip_logo() lo (đúng model: bỏ qua 2.5, xoá TẠI CHỖ, 1 file). Trước đây gọi thêm
    # auto_remove_watermark(replace=True) ở đây → chạy cho MỌI model, để lại bản .wmtmp dư (2 file/video)
    # và blur góc video 2.5 vốn sạch. Bỏ hẳn: một đường xoá logo duy nhất.
    return fname

