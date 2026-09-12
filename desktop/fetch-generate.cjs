// Fast in-Electron video generation: submit /chat/completion from the nick's OWN logged-in
// partition (persist:dola-<name>) so the bdms SDK signs the request; poll and download. No
// gateway, no patchright, no clicking. SUBMIT_JS / POLL_JS are copied verbatim from
// video_worker.py — keep them in sync if the Python changes.
const { BrowserWindow, session } = require("electron");
const { applyProxy, attachLoadErrorHandler, preflightDola } = require("./proxy.cjs");
const fs = require("fs");
const path = require("path");
const https = require("https");
const http = require("http");

const SUBMIT_JS = `
async ({prompt, ratio, duration, model, query, ackIdleMs}) => {
  const nowMs = Date.now();
  const nowSec = Math.floor(nowMs / 1000);
  const uuid = () => crypto.randomUUID();
  const localConv = "local_" + String(nowMs) + String(Math.floor(Math.random() * 1000)).padStart(3, "0");
  const replyFormat = "生成された動画：%s";
  const abilityParam = {model, duration: Number(duration),
    input_box_content: {user_input_content: prompt, reply_message_format: replyFormat}};
  if (ratio) abilityParam.ratio = ratio;

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
        content: {text_block: {text: replyFormat.replace("%s", prompt), icon_url: "", icon_url_dark: "", summary: ""},
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
      need_create_conversation: true, conversation_init_option: {need_ack_conversation: true},
      regen_query_id: [], edit_query_id: [], regen_instruction: "", no_replace_for_regen: false,
      message_from: 0, shared_app_name: "", shared_app_id: "",
      sse_recv_event_options: {support_chunk_delta: true}, is_ai_playground: false, is_old_user: false,
      recovery_option: {is_recovery: true, req_create_time_sec: nowSec, append_sse_event_scene: 0},
      message_storage_type: 0, related_deleted_message_ids: {}, connector_info_list: [],
      model_config: {model_item_key: "", model_extra_params: {}},
      aggregate_params: {conversation_mode: "", mode_id: "", model_item_key: "", agent_mode: "",
                         reasoning_effort: "", provider_id: ""},
    },
    chat_ability: {ability_type: 17, ability_param: JSON.stringify(abilityParam)},
    user_context: [],
    ext: {answer_with_suggest: "0", sub_conv_firstmet_type: "1", collection_id: "", is_finish: "1",
          conversation_init_option: '{"need_ack_conversation":true}', commerce_credit_config_enable: "0"},
  };

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
`;

const POLL_JS = `
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
`;

// Only models with a known ability_param key work over the request path. Seedance 2.5 needs an
// entitled account whose key we do not have yet -> tell the user to use the normal (UI) path.
function modelKeyFor(model) {
  const m = String(model || "").toLowerCase().replace(/[\s-]/g, "_");
  if (["seedance_2.0", "seedance_v2.0", "seedance_20", "seedance_v20"].includes(m)) return "seedance_v2.0";
  if (["seedance_2.5", "seedance_v2.5", "seedance_25", "seedance_v25"].includes(m)) return "seedance_v2.5";
  return "seedance_v2.0";
}

// Prefer the un-watermarked main_url (base64) from video_model.video_list, else the plain url.
function unwatermarkedUrl(videoModelStr, fallback) {
  try {
    const vm = JSON.parse(videoModelStr || "{}");
    const list = vm.video_list || {};
    const cands = [];
    for (const v of Object.values(list)) {
      if (!v || typeof v !== "object" || !v.main_url) continue;
      let decoded;
      try { decoded = Buffer.from(v.main_url, "base64").toString("utf8"); } catch (e) { continue; }
      if (decoded.startsWith("http")) cands.push([parseInt(v.bitrate || v.real_bitrate || 0, 10) || 0, decoded]);
    }
    cands.sort((a, b) => b[0] - a[0]);
    if (cands.length) return cands[0][1];
  } catch (e) {}
  return fallback;
}

function download(url, dest, redirects = 0) {
  return new Promise((resolve, reject) => {
    if (redirects > 5) return reject(new Error("too many redirects"));
    const mod = url.startsWith("https") ? https : http;
    mod.get(url, (res) => {
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        res.resume();
        return download(res.headers.location, dest, redirects + 1).then(resolve, reject);
      }
      if (res.statusCode !== 200) { res.resume(); return reject(new Error("HTTP " + res.statusCode)); }
      const file = fs.createWriteStream(dest);
      res.pipe(file);
      file.on("finish", () => file.close(() => resolve(dest)));
      file.on("error", (e) => { fs.unlink(dest, () => {}); reject(e); });
    }).on("error", reject);
  });
}

// Capture the API query the page itself uses (device_id, tea_uuid, web_id, region, ...).
function captureQuery(ses, ms = 15000) {
  return new Promise((resolve) => {
    let done = false;
    const filter = { urls: ["*://*.dola.com/*"] };
    const finish = (q) => { if (done) return; done = true; try { ses.webRequest.onBeforeRequest(filter, null); } catch (e) {} resolve(q); };
    ses.webRequest.onBeforeRequest(filter, (details, cb) => {
      cb({});
      if (!done && /[?&]device_id=/.test(details.url) && /[?&]web_id=/.test(details.url)) {
        const q = {};
        try { new URL(details.url).searchParams.forEach((v, k) => (q[k] = v)); } catch (e) { return; }
        finish(q);
      }
    });
    setTimeout(() => finish(null), ms);
  });
}

async function safeLoad(win, url) {
  try { await win.loadURL(url); }
  catch (e) { const c = (e && (e.code || e.errno)) || ""; if (String(c) !== "-3" && !/ERR_ABORTED/.test(String(e))) throw e; }
}

// Run an in-page async function robustly. We do NOT rely on executeJavaScript resolving a
// long-lived Promise across the isolated/main-world bridge (that intermittently fails with
// "Script failed to execute"). Instead the async work runs detached in the page and stores its
// result on window.__dolaR[token]; we poll that global with tiny synchronous reads.
async function runInPage(win, funcText, arg, timeoutMs = 130000) {
  const token = "t" + Date.now() + "_" + Math.floor(Math.random() * 1e6);
  const kick =
    "(() => { const __k = " + JSON.stringify(token) + "; window.__dolaR = window.__dolaR || {}; " +
    "window.__dolaR[__k] = { pending: true }; (async () => { try { " +
    "const __v = await (" + funcText + ")(" + JSON.stringify(arg) + "); window.__dolaR[__k] = { ok: true, value: __v }; } " +
    "catch (e) { window.__dolaR[__k] = { ok: false, error: String((e && e.stack) ? e.stack : e) }; } })(); return true; })()";
  try {
    await win.webContents.executeJavaScript(kick, true);
  } catch (e) {
    throw new Error("Không chạy được script trong trang: " + String(e && e.message ? e.message : e));
  }
  const read = "(window.__dolaR && window.__dolaR[" + JSON.stringify(token) + "]) || null";
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    await new Promise((r) => setTimeout(r, 700));
    let r = null;
    try { r = await win.webContents.executeJavaScript(read, true); } catch (e) { continue; }
    if (r && r.pending !== true) {
      try { await win.webContents.executeJavaScript("delete window.__dolaR[" + JSON.stringify(token) + "]", true); } catch (e) {}
      if (r.ok) return r.value;
      throw new Error("Trong trang Dola: " + (r.error || "không rõ"));
    }
  }
  throw new Error("Quá thời gian chờ kết quả trong trang Dola.");
}

// { name, prompt, model, duration, ratio, downloadsDir, showBrowser, onStep }
async function fetchGenerate(opts) {
  const { name, prompt, downloadsDir } = opts;
  const step = (m) => { try { if (opts.onStep) opts.onStep(m); } catch (e) {} };
  const modelKey = modelKeyFor(opts.model);
  if (!modelKey) throw new Error("Chế độ nhanh (HTTP) hiện chỉ hỗ trợ Seedance 2.0. Với 2.5 hãy dùng nút Tạo video thường.");
  const duration = Number(opts.duration) || 10;
  const partition = `persist:dola-${name}`;
  const ses = session.fromPartition(partition);
  // Cùng proxy với phía Python: không có thì mạng chặn dola.com và cửa sổ chỉ hiện trang trắng.
  // Bản đóng gói: .env.local và accounts/ nằm ở userData (main.js truyền dataDir), KHÔNG phải cạnh mã.
  const repoRoot = opts.dataDir || path.dirname(__dirname);
  let proxyInfo = null;
  try { proxyInfo = await applyProxy(ses, repoRoot, name, step); } catch (_) {}
  const pre = await preflightDola(ses, repoRoot, name);
  if (!pre.ok) throw new Error(pre.error);
  const win = new BrowserWindow({
    width: 480, height: 640, show: !!opts.showBrowser, title: `Tạo video (nhanh) — ${name}`,
    webPreferences: { partition, contextIsolation: true, nodeIntegration: false },
  });
  attachLoadErrorHandler(win, step, () => proxyInfo);
  try {
    step("Mở phiên Dola của nick…");
    // Seed the Electron partition from the nick's saved cookies (accounts/<name>/cookies.json),
    // so nicks imported by pasted cookie / gateway also work here, not only app-login nicks.
    try {
      const jf = path.join(repoRoot, "accounts", name, "cookies.json");
      if (fs.existsSync(jf)) {
        const saved = JSON.parse(fs.readFileSync(jf, "utf8"));
        for (const c of Array.isArray(saved) ? saved : []) {
          if (!c || !c.name) continue;
          const dom = (c.domain || ".dola.com").replace(/^\./, "");
          try {
            await ses.cookies.set({
              url: "https://www." + (dom.startsWith("dola.com") ? "dola.com" : dom),
              name: c.name, value: String(c.value == null ? "" : c.value),
              domain: c.domain || ".dola.com", path: c.path || "/",
              secure: c.secure !== false, httpOnly: !!c.httpOnly,
              expirationDate: Math.floor(Date.now() / 1000) + 180 * 86400,
            });
          } catch (e) {}
        }
      }
    } catch (e) {}
    const queryP = captureQuery(ses);
    await safeLoad(win, "https://www.dola.com/chat");
    await new Promise((r) => setTimeout(r, 4000)); // let the bdms SDK load & sign
    const query = await queryP;
    if (!query || !query.device_id) throw new Error("Không lấy được tham số phiên (device_id) — nick chưa đăng nhập đủ, đăng nhập lại.");
    // Wait for the bdms SDK to hook fetch (it signs the request with msToken + a_bogus).
    // Submitting before it loads makes Dola reject with 710010202 / the call throws.
    step("Chờ Dola sẵn sàng ký request…");
    for (let i = 0; i < 20; i++) {
      let ready = false;
      try { ready = await win.webContents.executeJavaScript("!!(window.bdms && (window.bdms.frontierSign || window.bdms.init))", true); } catch (e) {}
      if (ready) break;
      await new Promise((r) => setTimeout(r, 800));
    }
    const cookies = await ses.cookies.get({ url: "https://www.dola.com" });
    const cval = (n) => (cookies.find((c) => c.name === n) || {}).value || "";
    const msToken = cval("msToken"), fp = cval("s_v_web_id");
    if (!cval("sessionid")) throw new Error("Nick chưa đăng nhập (thiếu sessionid).");
    delete query.msToken; delete query.a_bogus; delete query.web_tab_id;
    query.fp = query.fp || fp; query.tz_name = query.tz_name || "Asia/Tokyo";

    step("Gửi lệnh tạo video (HTTP request)…");
    const args = { prompt, ratio: opts.ratio || "", duration, model: modelKey, query, ackIdleMs: 3000 };
    const submit = await runInPage(win, SUBMIT_JS, args);
    let convId = submit && submit.convId;
    if (!convId) {
      const errs = (submit && submit.errors) || [];
      const real = errs.find((e) => /"error_code"\s*:\s*[1-9]/.test(e));
      if (real) throw new Error("Dola từ chối lệnh: " + String(real).slice(0, 180));
      if (submit && submit.status === 200) {
        // Delivered but no id — do NOT re-submit (would double-charge). Recover from recent convs.
        step("Đã gửi, đang xác nhận hội thoại…");
        for (let i = 0; i < 6 && !convId; i++) {
          await new Promise((r) => setTimeout(r, 2000));
          try {
            const ids = await win.webContents.executeJavaScript(`(async()=>{const p=new URLSearchParams(${JSON.stringify(query)});p.set('web_tab_id',crypto.randomUUID());if(${JSON.stringify(msToken)})p.set('msToken',${JSON.stringify(msToken)});const r=await fetch('/im/chain/recent_conv?'+p,{method:'POST',headers:{'Content-Type':'application/json; encoding=utf-8','agw-js-conv':'str','Accept':'*/*'},credentials:'include',body:JSON.stringify({cmd:3200,uplink_body:{pull_recent_conv_chain_uplink_body:{limit:5,message_count_per_conv:1,api_version:1,conv_version:0,direction:3,option:{not_need_message:true,need_complete_conversation:true}}},sequence_id:crypto.randomUUID(),channel:2,version:'1'})});if(!r.ok)return[];const d=await r.json();const cells=(((d.downlink_body||{}).pull_recent_conv_chain_downlink_body)||{}).cells||[];return cells.map(c=>String((c.conversation||{}).conversation_id||c.id||'')).filter(x=>/^\d+$/.test(x));})()`, true);
            if (ids && ids.length) convId = ids[0];
          } catch (e) {}
        }
        if (!convId) throw new Error("Đã gửi tới Dola nhưng chưa xác nhận được hội thoại — KHÔNG gửi lại để tránh trừ lượt 2 lần. Kiểm tra dola.com.");
      } else {
        throw new Error("Gửi thất bại (HTTP " + (submit && submit.status) + ").");
      }
    }
    step("Đã gửi, conversation_id=" + convId + ". Đang chờ video…");
    const start = Date.now();
    const timeoutMs = (duration === 30 ? 1800 : 600) * 1000;
    while (Date.now() - start < timeoutMs) {
      await new Promise((r) => setTimeout(r, 5000));
      let poll;
      try { poll = await runInPage(win, POLL_JS, { conversationId: convId, msToken, fp }); }
      catch (e) { continue; }
      if (poll && poll.videos && poll.videos.length) {
        const url = unwatermarkedUrl((poll.videoModels || [])[0] || "", poll.videos[0]);
        step("Hoàn thành — đang tải về…");
        fs.mkdirSync(downloadsDir, { recursive: true });
        const dest = path.join(downloadsDir, name + "_" + Date.now() + ".mp4");
        await download(url, dest);
        return { ok: true, file: dest, conversationId: convId };
      }
      step("Đang tạo… (" + Math.round((Date.now() - start) / 1000) + "s)");
    }
    throw new Error("Hết giờ chờ mà chưa ra video (conversation_id=" + convId + ").");
  } finally {
    if (!win.isDestroyed()) win.close();
  }
}

module.exports = { fetchGenerate };
