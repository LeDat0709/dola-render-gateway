/**
 * ⚡ Dola 30 Sec By KHAN — Ultimate Background Service Worker Engine
 * 
 * Architecture & Features:
 * 1. 🛡️ Multi-Profile Cookie & Session Isolation Engine:
 *    - Independent tab cookie isolation via DeclarativeNetRequest (DNR).
 *    - Multi-account simultaneous execution without cookie collision.
 *    - Universal cookie parser (JSON array/map, Netscape format, raw Cookie headers).
 *    - Smart auto-rotator swapping accounts upon quota exhaustion.
 * 2. 🎬 Watermark-Free Raw 1080P Master Video Downloader:
 *    - Direct CDN master stream capture without compression/watermarks.
 *    - Atomic deduplication & concurrency locks preventing duplicate downloads.
 *    - Intelligent file tagging (timestamp, prompt slug, subfolder routing).
 *    - Rich OS notifications & toolbar badge status counter.
 * 3. 🔄 MV3 Ephemeral Lifecycle Resilience:
 *    - Session & active tab state persistence across service worker restarts.
 *    - Automatic dynamic DNR rule cleanup preventing rule leaks.
 *    - Instant script auto-injection into active tabs upon extension reload.
 */

'use strict';

/* =========================================================================
   1. CONSTANTS, STORAGE KEYS & DEFAULT CONFIGURATIONS
   ========================================================================= */

const ZDOLA_STORAGE_KEYS = {
  config: 'zdola_auto_download_config',
  history: 'zdola_download_history',
  downloadedSet: 'zdola_downloaded_urls',
  multiProfiles: 'multi_profiles',
  activeProfileName: 'active_profile_name',
  tabSessions: 'tab_sessions_cache'
};

const ZDOLA_DEFAULT_CONFIG = {
  autoDownload: true,
  notifications: true,
  subfolder: 'ZDola_Videos',
  qualityPreference: '1080P',
  totalDownloaded: 0
};

const SESSION_COLORS = [
  '#8b5cf6', // Purple (Khan signature)
  '#3b82f6', // Blue
  '#10b981', // Emerald
  '#f59e0b', // Amber
  '#ec4899', // Pink
  '#06b6d4', // Cyan
  '#6366f1'  // Indigo
];

/* =========================================================================
   2. IN-MEMORY STATE WITH MV3 PERSISTENCE
   ========================================================================= */

let zdolaConfig = { ...ZDOLA_DEFAULT_CONFIG };
let zdolaDownloadHistory = [];
let zdolaDownloadedKeys = new Set();
const zdolaInProgressKeys = new Set();
const recentDownloadDebounce = new Map(); // key -> timestamp

// tabId -> { profileName, cookies, cookieHeaderString, color }
const tabSessionMap = new Map();

function getNextColor(index) {
  return SESSION_COLORS[Math.abs(index) % SESSION_COLORS.length];
}

/**
 * Load persisted extension state from chrome.storage.local & session
 */
async function loadFullState() {
  try {
    const data = await chrome.storage.local.get([
      ZDOLA_STORAGE_KEYS.config,
      ZDOLA_STORAGE_KEYS.history,
      ZDOLA_STORAGE_KEYS.downloadedSet,
      ZDOLA_STORAGE_KEYS.tabSessions
    ]);

    if (data[ZDOLA_STORAGE_KEYS.config]) {
      zdolaConfig = { ...ZDOLA_DEFAULT_CONFIG, ...data[ZDOLA_STORAGE_KEYS.config] };
    }
    if (Array.isArray(data[ZDOLA_STORAGE_KEYS.history])) {
      zdolaDownloadHistory = data[ZDOLA_STORAGE_KEYS.history];
    }
    if (Array.isArray(data[ZDOLA_STORAGE_KEYS.downloadedSet])) {
      zdolaDownloadedKeys = new Set(data[ZDOLA_STORAGE_KEYS.downloadedSet]);
    }

    // Restore tab session map across SW wake-ups
    if (data[ZDOLA_STORAGE_KEYS.tabSessions] && typeof data[ZDOLA_STORAGE_KEYS.tabSessions] === 'object') {
      for (const [tabIdStr, session] of Object.entries(data[ZDOLA_STORAGE_KEYS.tabSessions])) {
        const tabId = parseInt(tabIdStr, 10);
        if (tabId && session) {
          tabSessionMap.set(tabId, session);
        }
      }
    }

    updateBadge();
  } catch (err) {
    console.error('[ZDola Background] Failed to load state:', err);
  }
}

/**
 * Save state back to storage
 */
async function saveDownloadState() {
  try {
    await chrome.storage.local.set({
      [ZDOLA_STORAGE_KEYS.config]: zdolaConfig,
      [ZDOLA_STORAGE_KEYS.history]: zdolaDownloadHistory.slice(0, 200),
      [ZDOLA_STORAGE_KEYS.downloadedSet]: Array.from(zdolaDownloadedKeys).slice(-600)
    });
  } catch (err) {
    console.error('[ZDola Background] Failed to save download state:', err);
  }
}

async function saveTabSessionsState() {
  try {
    const serialized = {};
    for (const [tabId, session] of tabSessionMap.entries()) {
      serialized[tabId] = session;
    }
    await chrome.storage.local.set({ [ZDOLA_STORAGE_KEYS.tabSessions]: serialized });
  } catch (err) {
    console.error('[ZDola Background] Failed to save tab sessions state:', err);
  }
}

function updateBadge() {
  try {
    if (!zdolaConfig.autoDownload) {
      chrome.action.setBadgeText({ text: 'OFF' });
      chrome.action.setBadgeBackgroundColor({ color: '#6b7280' });
    } else {
      const count = zdolaDownloadHistory.length;
      chrome.action.setBadgeText({ text: count > 0 ? String(count) : 'ON' });
      chrome.action.setBadgeBackgroundColor({ color: '#8b5cf6' });
    }
  } catch {}
}

/* =========================================================================
   3. UNIVERSAL COOKIE PARSER & COOKIE JAR CONTROLLER
   ========================================================================= */

/**
 * Universal parser supporting:
 * 1. JSON Array of cookies: [{ name, value, domain, ... }]
 * 2. JSON Object map: { "sessionid": "xyz", ... } or { cookies: [...] }
 * 3. Netscape HTTP Cookie File (.txt tab-separated format)
 * 4. Raw Header string: "name=value; name2=val2"
 */
function parseCookiesUniversal(cookieStr) {
  if (!cookieStr || typeof cookieStr !== 'string') {
    if (Array.isArray(cookieStr)) return cookieStr;
    return [];
  }

  const raw = cookieStr.trim();
  let rawCookies = [];

  // Try JSON format
  if (raw.startsWith('{') || raw.startsWith('[')) {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) {
        rawCookies = parsed;
      } else if (parsed && typeof parsed === 'object') {
        if (Array.isArray(parsed.cookies)) {
          rawCookies = parsed.cookies;
        } else if (Array.isArray(parsed.data)) {
          rawCookies = parsed.data;
        } else {
          // Key-Value map
          rawCookies = Object.entries(parsed).map(([k, v]) => ({
            name: k,
            value: typeof v === 'object' ? JSON.stringify(v) : String(v)
          }));
        }
      }
    } catch {}
  }

  // If not JSON, check Netscape or Header string
  if (rawCookies.length === 0) {
    const lines = raw.split(/\r?\n/);
    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;

      const cols = trimmed.split(/\t+|\s{2,}/);
      if (cols.length >= 7) {
        // Netscape format: domain, flag, path, secure, expiration, name, value
        rawCookies.push({
          domain: cols[0].trim(),
          httpOnly: cols[1]?.trim().toLowerCase() === 'true',
          path: cols[2]?.trim() || '/',
          secure: cols[3]?.trim().toLowerCase() === 'true',
          expires: parseFloat(cols[4]?.trim()) || 0,
          name: cols[5]?.trim(),
          value: cols.slice(6).join('\t').trim()
        });
      } else if (trimmed.includes('=')) {
        // Raw cookie header: "a=b; c=d"
        const pairs = trimmed.split(';');
        for (const pair of pairs) {
          const idx = pair.indexOf('=');
          if (idx > 0) {
            rawCookies.push({
              name: pair.substring(0, idx).trim(),
              value: pair.substring(idx + 1).trim()
            });
          }
        }
      }
    }
  }

  // Normalize all cookies
  const nowSec = Math.floor(Date.now() / 1000);
  const oneYearLater = nowSec + 31536000;
  const normalized = [];

  for (const c of rawCookies) {
    if (!c) continue;
    const name = String(c.name || c.key || '').trim();
    const value = String(c.value !== undefined ? c.value : '').trim();
    if (!name) continue;

    let domain = String(c.domain || '.dola.com').trim().toLowerCase();
    if (!domain.includes('dola.com') && !domain.includes('doubao.com') && !domain.includes('seaart.ai')) {
      domain = '.dola.com';
    }

    let sameSite = 'no_restriction';
    const rawSameSite = String(c.sameSite || '').toLowerCase();
    if (rawSameSite === 'lax' || rawSameSite === 'strict') {
      sameSite = rawSameSite;
    }

    const secure = sameSite === 'no_restriction' ? true : (c.secure !== undefined ? Boolean(c.secure) : true);
    const httpOnly = c.httpOnly !== undefined ? Boolean(c.httpOnly) : false;

    let expirationDate = typeof c.expirationDate === 'number'
      ? c.expirationDate
      : (typeof c.expires === 'number' ? c.expires : oneYearLater);

    if (expirationDate < nowSec) {
      expirationDate = oneYearLater;
    }

    normalized.push({
      name,
      value,
      domain,
      path: c.path || '/',
      expirationDate: Math.floor(expirationDate),
      httpOnly,
      secure,
      sameSite
    });
  }

  return normalized;
}

/**
 * Builds a valid target URL for chrome.cookies.set()
 */
function getCookieUrl(cookie) {
  let domain = String(cookie.domain || 'dola.com').toLowerCase();
  if (domain.startsWith('.')) {
    domain = domain.substring(1);
  }
  const isSecure = cookie.secure || cookie.sameSite === 'no_restriction';
  const scheme = isSecure ? 'https://' : 'http://';
  const path = cookie.path || '/';
  return `${scheme}${domain}${path.startsWith('/') ? path : '/' + path}`;
}

/**
 * Completely clears cookies for Dola, Doubao and related AI services
 */
async function clearAllDolaCookies() {
  return new Promise((resolve) => {
    const promises = [];
    const domainsToClear = ['dola.com', 'doubao.com', 'seaart.ai', 'byteintl.com', 'ibytedtos.com'];

    let pendingQueries = domainsToClear.length;
    if (pendingQueries === 0) return resolve();

    domainsToClear.forEach(domain => {
      chrome.cookies.getAll({ domain }, (cookies) => {
        if (cookies && cookies.length > 0) {
          for (const c of cookies) {
            const scheme = c.secure ? 'https://' : 'http://';
            const cleanDomain = c.domain.startsWith('.') ? c.domain.substring(1) : c.domain;
            const url = `${scheme}${cleanDomain}${c.path || '/'}`;
            promises.push(
              new Promise(res => chrome.cookies.remove({ url, name: c.name, storeId: c.storeId }, res))
            );
          }
        }
        pendingQueries--;
        if (pendingQueries === 0) {
          Promise.all(promises).then(resolve).catch(resolve);
        }
      });
    });
  });
}

/**
 * Sets a profile's cookie list into Chrome cookie jar
 */
async function setDolaCookies(cookies) {
  if (!Array.isArray(cookies) || cookies.length === 0) return;

  const nowSec = Math.floor(Date.now() / 1000);
  const tasks = cookies.map(c => {
    if (!c.name || c.value === undefined) return Promise.resolve();

    const targetUrl = getCookieUrl(c);
    let domainVal = c.domain ? String(c.domain).trim() : undefined;

    const cookieDetails = {
      url: targetUrl,
      name: String(c.name).trim(),
      value: String(c.value),
      path: c.path || '/',
      secure: c.sameSite === 'no_restriction' ? true : (c.secure !== undefined ? Boolean(c.secure) : true),
      httpOnly: Boolean(c.httpOnly),
      sameSite: c.sameSite || 'no_restriction',
      expirationDate: (c.expirationDate && c.expirationDate > nowSec)
        ? Math.floor(c.expirationDate)
        : (nowSec + 31536000)
    };

    if (domainVal && domainVal.startsWith('.')) {
      cookieDetails.domain = domainVal;
    }

    return new Promise(resolve => {
      chrome.cookies.set(cookieDetails, (result) => {
        if (chrome.runtime.lastError || !result) {
          // Fallback retry without domain field (lets Chrome derive it from URL)
          delete cookieDetails.domain;
          chrome.cookies.set(cookieDetails, () => resolve());
        } else {
          resolve();
        }
      });
    });
  });

  await Promise.all(tasks);
}

/* =========================================================================
   4. TAB SESSION ISOLATION ENGINE (DECLARATIVENETREQUEST)
   ========================================================================= */

/**
 * Injects a specific profile's cookies directly into HTTP headers for a given tabId.
 * This ensures multi-tab simultaneous multi-account operation without cross-contamination.
 */
async function applyTabSessionRule(tabId, profile) {
  if (!tabId || !profile || !profile.cookies) return;

  const cookieHeaderString = profile.cookies
    .map(c => `${c.name}=${c.value}`)
    .join('; ');

  tabSessionMap.set(tabId, {
    profileName: profile.name,
    cookies: profile.cookies,
    cookieHeaderString,
    color: profile.color || SESSION_COLORS[0]
  });

  await saveTabSessionsState();

  if (chrome.declarativeNetRequest && chrome.declarativeNetRequest.updateDynamicRules) {
    try {
      await chrome.declarativeNetRequest.updateDynamicRules({
        removeRuleIds: [tabId],
        addRules: [
          {
            id: tabId,
            priority: 1000,
            action: {
              type: 'modifyHeaders',
              requestHeaders: [
                {
                  header: 'Cookie',
                  operation: 'set',
                  value: cookieHeaderString
                }
              ]
            },
            condition: {
              urlFilter: '*dola.com*',
              tabIds: [tabId],
              resourceTypes: [
                'main_frame',
                'sub_frame',
                'stylesheet',
                'script',
                'image',
                'font',
                'object',
                'xmlhttprequest',
                'ping',
                'other'
              ]
            }
          }
        ]
      });
      console.log(`[ZDola Session] 🛡️ Isolated Tab Session rule applied for Tab #${tabId} [Profile: ${profile.name}]`);
    } catch (err) {
      console.warn(`[ZDola Session] Failed to update dynamic rule for Tab #${tabId}:`, err);
    }
  }
}

// Clean up tab rules and sessions when tab closes
chrome.tabs.onRemoved.addListener((tabId) => {
  if (tabSessionMap.has(tabId)) {
    tabSessionMap.delete(tabId);
    saveTabSessionsState();
    if (chrome.declarativeNetRequest && chrome.declarativeNetRequest.updateDynamicRules) {
      chrome.declarativeNetRequest.updateDynamicRules({
        removeRuleIds: [tabId]
      }).catch(() => {});
    }
  }
});

/* =========================================================================
   5. AUTO-ROTATOR ENGINE (SMART ACCOUNT SWAPPING)
   ========================================================================= */

/**
 * Automatically rotates to the next saved profile when quota is exhausted or on demand
 */
async function autoRotateProfile(tabId) {
  const store = await chrome.storage.local.get([
    ZDOLA_STORAGE_KEYS.multiProfiles,
    ZDOLA_STORAGE_KEYS.activeProfileName
  ]);

  const profilesObj = store[ZDOLA_STORAGE_KEYS.multiProfiles] || {};
  const profileKeys = Object.keys(profilesObj);

  if (profileKeys.length <= 1) {
    console.log('[ZDola Auto-Rotator] Only 1 or 0 profiles available, skipping rotation.');
    return { success: false, reason: 'Not enough profiles for rotation' };
  }

  let currentProfileName = store[ZDOLA_STORAGE_KEYS.activeProfileName];
  if (tabId && tabSessionMap.has(tabId)) {
    currentProfileName = tabSessionMap.get(tabId).profileName;
  }

  const currentIndex = profileKeys.indexOf(currentProfileName);
  const nextIndex = (currentIndex + 1) % profileKeys.length;
  const nextProfileName = profileKeys[nextIndex];
  const nextProfile = profilesObj[nextProfileName];

  if (!nextProfile) {
    return { success: false, reason: 'Next profile not found' };
  }

  console.log(`[ZDola Auto-Rotator] 🔄 Auto-Swapping from [${currentProfileName}] -> [${nextProfileName}]`);

  if (tabId) {
    await applyTabSessionRule(tabId, nextProfile);
  }
  await setDolaCookies(nextProfile.cookies);
  await chrome.storage.local.set({ [ZDOLA_STORAGE_KEYS.activeProfileName]: nextProfileName });

  if (tabId) {
    chrome.tabs.reload(tabId);
  }

  if (zdolaConfig.notifications) {
    chrome.notifications.create({
      type: 'basic',
      iconUrl: 'icon128.png',
      title: '🔄 ZDola Auto-Swapped Account!',
      message: `Rotated to Profile: ${nextProfileName}`,
      priority: 1
    });
  }

  return { success: true, from: currentProfileName, to: nextProfileName };
}

/* =========================================================================
   6. UNIFIED RAW 1080P MASTER VIDEO DOWNLOADER ENGINE
   ========================================================================= */

function sanitizeFilename(text) {
  return String(text || '')
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, '_')
    .replace(/\s+/g, '_')
    .replace(/_+/g, '_')
    .substring(0, 50)
    .trim();
}

function generateVideoFilename(video) {
  const now = new Date();
  const dateStr = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const timeStr = `${String(now.getHours()).padStart(2, '0')}-${String(now.getMinutes()).padStart(2, '0')}-${String(now.getSeconds()).padStart(2, '0')}`;

  let promptSlug = '';
  const candidate = video.prompt || video.topicTitle || video.title || '';
  if (candidate && !candidate.includes('Dola AI - Your everyday')) {
    const slug = sanitizeFilename(candidate);
    if (slug) promptSlug = `_${slug}`;
  }

  const baseFolder = zdolaConfig.subfolder
    ? `${zdolaConfig.subfolder.replace(/[/\\?%*:|"<>]/g, '_')}/`
    : 'ZDola_Videos/';

  return `${baseFolder}ZDola_${dateStr}_${timeStr}${promptSlug}_1080P_raw.mp4`;
}

/**
 * Unified download controller with atomic lock, deduplication, and notification
 */
async function handleVideoDownload(video, force = false) {
  if (!video || !video.url) {
    return { ok: false, error: 'Invalid video URL' };
  }

  const cleanUrl = String(video.url).trim();
  if (!cleanUrl.startsWith('http')) {
    return { ok: false, error: 'Non-http video URL' };
  }

  // Strict check: unwatermarked fallback_api or manual force
  if (video.source && video.source !== 'fallback_api' && !force) {
    return { ok: true, downloaded: false, reason: 'Ignored non-fallback preview stream' };
  }

  if (!zdolaConfig.autoDownload && !force) {
    return { ok: true, downloaded: false, reason: 'Auto-download is currently disabled' };
  }

  const mediaKey = String(video.vid || cleanUrl);
  const now = Date.now();

  // Debounce check (prevent duplicate calls within 5 seconds)
  if (!force) {
    if (zdolaDownloadedKeys.has(mediaKey) || zdolaDownloadedKeys.has(cleanUrl)) {
      return { ok: true, downloaded: false, reason: 'Already downloaded' };
    }
    if (zdolaInProgressKeys.has(mediaKey)) {
      return { ok: true, downloaded: false, reason: 'Download in progress' };
    }
    const lastTime = recentDownloadDebounce.get(cleanUrl);
    if (lastTime && now - lastTime < 5000) {
      return { ok: true, downloaded: false, reason: 'Debounced duplicate download' };
    }
  }

  recentDownloadDebounce.set(cleanUrl, now);
  zdolaInProgressKeys.add(mediaKey);
  zdolaDownloadedKeys.add(mediaKey);
  zdolaDownloadedKeys.add(cleanUrl);

  const filename = video.filename || generateVideoFilename(video);

  try {
    console.log('[ZDola Downloader] 🚀 Starting 1080P Watermark-Free Download:', filename, cleanUrl);

    const downloadId = await chrome.downloads.download({
      url: cleanUrl,
      filename,
      saveAs: false,
      conflictAction: 'uniquify'
    });

    zdolaInProgressKeys.delete(mediaKey);
    zdolaConfig.totalDownloaded = (zdolaConfig.totalDownloaded || 0) + 1;

    const historyEntry = {
      id: `${Date.now()}_${Math.random().toString(36).substring(2, 7)}`,
      downloadId,
      url: cleanUrl,
      filename,
      prompt: video.prompt || video.topicTitle || video.title || 'ZDola 1080P Video',
      resolution: '1080P Raw (No Watermark)',
      source: video.source || 'fallback_api',
      timestamp: Date.now()
    };

    zdolaDownloadHistory.unshift(historyEntry);
    await saveDownloadState();
    updateBadge();

    // Broadcast completion to popup if open
    chrome.runtime.sendMessage({
      type: 'AUTO_DOWNLOAD_COMPLETED',
      filename,
      downloadId,
      prompt: historyEntry.prompt
    }).catch(() => {});

    // Advance Batch Queue if running
    onBatchQueueItemCompleted().catch(e => console.warn('[ZDola Queue Error]:', e));

    if (zdolaConfig.notifications) {
      try {
        chrome.notifications.create({
          type: 'basic',
          iconUrl: 'icon128.png',
          title: '🎬 Video Downloaded (100% Raw No Watermark)!',
          message: `${historyEntry.prompt.substring(0, 45)}...\nSaved to Downloads/${filename}`,
          priority: 1
        });
      } catch {}
    }

    return { ok: true, downloaded: true, downloadId, filename };
  } catch (err) {
    zdolaInProgressKeys.delete(mediaKey);
    console.error('[ZDola Downloader] Download execution failed:', err);
    return { ok: false, error: err.message || String(err) };
  }
}

/* =========================================================================
   7. BATCH QUEUE & SMART QUOTA AUTO-ROTATION CONTROLLER
   ========================================================================= */

/**
 * Dispatches a prompt directly into active or open Dola tab
 */
async function dispatchPromptToDolaTab(promptText, ratio = '9:16') {
  if (!promptText) return;
  const tabs = await chrome.tabs.query({ url: ['*://*.dola.com/*', '*://*.dola.ai/*', '*://*.doubao.com/*'] });
  let targetTab = tabs.find(t => t.active) || tabs[0];

  if (!targetTab) {
    targetTab = await new Promise(res => chrome.tabs.create({ url: 'https://www.dola.com', active: true }, res));
    await new Promise(res => setTimeout(res, 4500));
  }

  chrome.tabs.sendMessage(targetTab.id, {
    type: 'INJECT_DOLA_TASK',
    prompt: promptText,
    ratio: ratio || '9:16',
    duration: 30
  }, (res) => {
    if (chrome.runtime.lastError || !res?.ok) {
      chrome.scripting.executeScript({
        target: { tabId: targetTab.id },
        func: (p, r) => {
          const textarea = document.querySelector('textarea.semi-input-textarea, textarea, div[contenteditable="true"], [role="textbox"]');
          if (textarea) {
            if (textarea.tagName === 'DIV' || textarea.isContentEditable) {
              textarea.innerText = p;
            } else {
              textarea.value = p;
            }
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.dispatchEvent(new Event('change', { bubbles: true }));
            window.postMessage({ type: 'PURZA_UPDATE_SETTINGS', ratio: r, duration: 30 }, '*');
            setTimeout(() => {
              const btn = document.querySelector('button[type="submit"], [data-testid="chat_input_send_button"], div[role="button"][class*="send"], button[class*="send"]')?.closest('button, div[role="button"]');
              if (btn && !btn.disabled) btn.click();
            }, 350);
          }
        },
        args: [promptText, ratio]
      }).catch(() => {});
    }
  });
}

/**
 * Automatically triggers next prompt in queue upon successful video download
 */
async function onBatchQueueItemCompleted() {
  const store = await chrome.storage.local.get(['zdola_batch_queue']);
  const queue = store.zdola_batch_queue;
  if (!queue || queue.status !== 'running') return;

  const nextIndex = (queue.currentIndex || 0) + 1;
  const cooldownSec = queue.cooldownSec || 8;
  const cooldownMs = cooldownSec * 1000;

  if (nextIndex < queue.prompts.length) {
    queue.currentIndex = nextIndex;
    await chrome.storage.local.set({ zdola_batch_queue: queue });

    chrome.runtime.sendMessage({
      type: 'BATCH_QUEUE_PROGRESS',
      currentIndex: nextIndex,
      total: queue.prompts.length,
      nextPrompt: queue.prompts[nextIndex],
      cooldownSec
    }).catch(() => {});

    console.log(`[ZDola Batch Queue] ⏳ Cooldown ${cooldownSec}s before Prompt #${nextIndex + 1} / ${queue.prompts.length}...`);

    setTimeout(async () => {
      const refreshedStore = await chrome.storage.local.get(['zdola_batch_queue']);
      const activeQueue = refreshedStore.zdola_batch_queue;
      if (activeQueue && activeQueue.status === 'running') {
        dispatchPromptToDolaTab(activeQueue.prompts[activeQueue.currentIndex], activeQueue.activeRatio || '9:16');
      }
    }, cooldownMs);
  } else {
    // Batch Completed
    queue.status = 'completed';
    await chrome.storage.local.set({ zdola_batch_queue: queue });

    chrome.runtime.sendMessage({
      type: 'BATCH_QUEUE_COMPLETED',
      total: queue.prompts.length
    }).catch(() => {});

    if (zdolaConfig.notifications) {
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icon128.png',
        title: '🎉 Hàng Đợi Hoàn Thành!',
        message: `Đã tự động tạo và tải xong ${queue.prompts.length} video HD không watermark!`,
        priority: 2
      });
    }
  }
}

/**
 * Handles quota limit detection: auto-rotates to next account and resumes queue
 */
async function handleQuotaDetected(tabId, reason) {
  console.warn(`[ZDola Quota Guard] ⚠️ Limit detected (${reason}). Starting auto-rotation...`);

  const store = await chrome.storage.local.get([
    'zdola_batch_queue',
    ZDOLA_STORAGE_KEYS.multiProfiles
  ]);

  const queue = store.zdola_batch_queue;
  const profiles = store[ZDOLA_STORAGE_KEYS.multiProfiles] || {};
  const profileKeys = Object.keys(profiles);

  if (profileKeys.length <= 1) {
    if (zdolaConfig.notifications) {
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icon128.png',
        title: '⚠️ Dola Hết Hạn Mức (Daily Limit Reached)',
        message: 'Tài khoản hiện tại đã hết hạn mức tạo video. Hãy thêm tài khoản khác trong extension!',
        priority: 2
      });
    }
    return;
  }

  // Auto rotate to next account
  const rotateRes = await autoRotateProfile(tabId);
  if (rotateRes.success) {
    if (zdolaConfig.notifications) {
      chrome.notifications.create({
        type: 'basic',
        iconUrl: 'icon128.png',
        title: '🔄 Đã Tự Động Xoay Tài Khoản!',
        message: `Đổi sang [${rotateRes.to}] do nick cũ hết hạn mức. Đang tiếp tục tác vụ...`,
        priority: 1
      });
    }

    // If batch queue is running, retry the current prompt on the newly loaded account
    if (queue && queue.status === 'running') {
      const targetPrompt = queue.prompts[queue.currentIndex];
      console.log(`[ZDola Batch Queue] 🔄 Retrying prompt #${queue.currentIndex + 1} with new account [${rotateRes.to}]...`);

      const onReloaded = (updatedTabId, changeInfo) => {
        if (updatedTabId === tabId && changeInfo.status === 'complete') {
          chrome.tabs.onUpdated.removeListener(onReloaded);
          setTimeout(() => {
            dispatchPromptToDolaTab(targetPrompt, queue.activeRatio || '9:16');
          }, 4500); // Allow Dola interface to initialize
        }
      };
      chrome.tabs.onUpdated.addListener(onReloaded);
    }
  }
}

/* =========================================================================
   7. AUTO INJECTION INTO RUNNING TABS
   ========================================================================= */

async function autoInjectIntoExistingTabs() {
  try {
    const tabs = await chrome.tabs.query({
      url: [
        'https://*.dola.com/*',
        'https://*.doubao.com/*',
        'https://*.seaart.ai/*'
      ]
    });

    for (const tab of tabs) {
      if (!tab.id) continue;
      try {
        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['extractor.js'],
          world: 'MAIN'
        }).catch(() => {});

        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          files: ['content.js']
        }).catch(() => {});
      } catch {}
    }
  } catch (err) {
    console.warn('[ZDola Background] autoInject failed:', err);
  }
}

/* =========================================================================
   8. UNIFIED RUNTIME MESSAGE ROUTER
   ========================================================================= */

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (!message) return false;

  const action = message.action || message.type;
  if (!action) return false;

  // --- A. VIDEO DOWNLOAD COMMANDS ---
  if (
    action === 'AUTO_DOWNLOAD_VIDEO' ||
    action === 'download_video' ||
    action === 'download' ||
    action === 'fast_download'
  ) {
    const videoData = message.video || {
      url: message.url,
      filename: message.filename,
      prompt: message.prompt || message.title || '',
      source: message.source || 'fallback_api'
    };
    handleVideoDownload(videoData, Boolean(message.force)).then(sendResponse);
    return true;
  }

  if (action === 'TRIGGER_PAGE_SCAN_AND_DOWNLOAD') {
    (async () => {
      try {
        const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!activeTab || !activeTab.id) {
          return sendResponse({ ok: false, error: 'No active tab found' });
        }

        await chrome.scripting.executeScript({
          target: { tabId: activeTab.id },
          files: ['extractor.js'],
          world: 'MAIN'
        }).catch(() => {});
        await chrome.scripting.executeScript({
          target: { tabId: activeTab.id },
          files: ['content.js']
        }).catch(() => {});

        const scanRes = await chrome.tabs.sendMessage(activeTab.id, { type: 'SCAN_AND_DOWNLOAD_ACTIVE_TAB' });
        if (scanRes?.ok && scanRes.foundCount > 0) {
          sendResponse({
            ok: true,
            downloadedCount: scanRes.foundCount,
            unwatermarked: scanRes.unwatermarked,
            url: scanRes.url
          });
        } else {
          sendResponse({
            ok: false,
            downloadedCount: 0,
            message: scanRes?.message || 'Unwatermarked 1080P stream not ready yet.'
          });
        }
      } catch (err) {
        sendResponse({ ok: false, error: err.message || String(err) });
      }
    })();
    return true;
  }

  // --- B. MULTI-PROFILE & SESSION MANAGEMENT ---
  if (action === 'clear_cookies') {
    clearAllDolaCookies().then(() => {
      sendResponse({ status: 'success' });
    });
    return true;
  }

  if (action === 'save_profile_from_browser') {
    (async () => {
      try {
        const profileName = message.profileName || `Profile_${Date.now().toString().slice(-4)}`;
        const cookies = await new Promise(res => {
          chrome.cookies.getAll({ domain: 'dola.com' }, (c1) => {
            chrome.cookies.getAll({ url: 'https://www.dola.com' }, (c2) => {
              chrome.cookies.getAll({ domain: 'doubao.com' }, (c3) => {
                const map = new Map();
                (c1 || []).forEach(c => map.set(`${c.name}_${c.domain}`, c));
                (c2 || []).forEach(c => map.set(`${c.name}_${c.domain}`, c));
                (c3 || []).forEach(c => map.set(`${c.name}_${c.domain}`, c));
                res(Array.from(map.values()));
              });
            });
          });
        });

        if (cookies.length === 0) {
          return sendResponse({ success: false, error: 'No active cookies found in browser' });
        }

        const data = await chrome.storage.local.get([ZDOLA_STORAGE_KEYS.multiProfiles]);
        const profiles = data[ZDOLA_STORAGE_KEYS.multiProfiles] || {};
        const count = Object.keys(profiles).length;

        profiles[profileName] = {
          name: profileName,
          cookies,
          cookieCount: cookies.length,
          color: getNextColor(count),
          updatedAt: new Date().toISOString()
        };

        await chrome.storage.local.set({
          [ZDOLA_STORAGE_KEYS.multiProfiles]: profiles,
          [ZDOLA_STORAGE_KEYS.activeProfileName]: profileName
        });

        sendResponse({ success: true, profileName, cookieCount: cookies.length });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    })();
    return true;
  }

  if (action === 'import_profile_cookies') {
    (async () => {
      try {
        const profileName = message.profileName || `Profile_${Date.now().toString().slice(-4)}`;
        const cookieStr = message.cookieString || message.cookies || '';
        const parsed = parseCookiesUniversal(cookieStr);

        if (parsed.length === 0) {
          return sendResponse({ success: false, error: 'Invalid or empty cookies format' });
        }

        const data = await chrome.storage.local.get([ZDOLA_STORAGE_KEYS.multiProfiles]);
        const profiles = data[ZDOLA_STORAGE_KEYS.multiProfiles] || {};
        const count = Object.keys(profiles).length;

        profiles[profileName] = {
          name: profileName,
          cookies: parsed,
          cookieCount: parsed.length,
          color: getNextColor(count),
          updatedAt: new Date().toISOString()
        };

        await chrome.storage.local.set({ [ZDOLA_STORAGE_KEYS.multiProfiles]: profiles });
        sendResponse({ success: true, profileName, cookieCount: parsed.length });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    })();
    return true;
  }

  if (action === 'switch_profile') {
    (async () => {
      try {
        const targetProfileName = message.profileName;
        const data = await chrome.storage.local.get([ZDOLA_STORAGE_KEYS.multiProfiles]);
        const profiles = data[ZDOLA_STORAGE_KEYS.multiProfiles] || {};
        const targetProfile = profiles[targetProfileName];

        if (!targetProfile || !targetProfile.cookies || targetProfile.cookies.length === 0) {
          return sendResponse({ success: false, error: 'Profile not found or has no cookies' });
        }

        const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (activeTab && activeTab.id) {
          await applyTabSessionRule(activeTab.id, targetProfile);
          await clearAllDolaCookies();
          await setDolaCookies(targetProfile.cookies);
          await chrome.storage.local.set({ [ZDOLA_STORAGE_KEYS.activeProfileName]: targetProfileName });
          chrome.tabs.reload(activeTab.id);
          sendResponse({ success: true, profileName: targetProfileName, tabId: activeTab.id });
        } else {
          sendResponse({ success: false, error: 'No active tab found' });
        }
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    })();
    return true;
  }

  if (action === 'open_profile_new_tab' || action === 'open_account_new_tab') {
    (async () => {
      try {
        const profileName = message.profileName;
        const data = await chrome.storage.local.get([ZDOLA_STORAGE_KEYS.multiProfiles]);
        const profiles = data[ZDOLA_STORAGE_KEYS.multiProfiles] || {};
        const profile = profiles[profileName];

        if (!profile || !profile.cookies || profile.cookies.length === 0) {
          // Open normal tab if no profile specified
          chrome.tabs.create({ url: 'https://www.dola.com', active: true }, (newTab) => {
            sendResponse({ success: true, tabId: newTab.id });
          });
          return;
        }

        chrome.tabs.create({ url: 'https://www.dola.com', active: true }, async (newTab) => {
          await applyTabSessionRule(newTab.id, profile);
          await setDolaCookies(profile.cookies);
          await chrome.storage.local.set({ [ZDOLA_STORAGE_KEYS.activeProfileName]: profileName });
          sendResponse({ success: true, tabId: newTab.id, profileName });
        });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    })();
    return true;
  }

  if (action === 'auto_rotate_profile' || action === 'auto_swap_profile') {
    const tabId = sender.tab ? sender.tab.id : null;
    autoRotateProfile(tabId).then(sendResponse);
    return true;
  }

  if (action === 'get_active_tab_session') {
    const tabId = sender.tab ? sender.tab.id : null;
    if (tabId && tabSessionMap.has(tabId)) {
      sendResponse({ isolated: true, session: tabSessionMap.get(tabId) });
    } else {
      chrome.storage.local.get([ZDOLA_STORAGE_KEYS.activeProfileName], (res) => {
        sendResponse({
          isolated: false,
          session: {
            profileName: res[ZDOLA_STORAGE_KEYS.activeProfileName] || 'Default',
            color: SESSION_COLORS[0]
          }
        });
      });
    }
    return true;
  }

  if (action === 'delete_profile') {
    (async () => {
      try {
        const profileNameToDelete = message.profileName;
        const data = await chrome.storage.local.get([
          ZDOLA_STORAGE_KEYS.multiProfiles,
          ZDOLA_STORAGE_KEYS.activeProfileName
        ]);
        const profiles = data[ZDOLA_STORAGE_KEYS.multiProfiles] || {};
        delete profiles[profileNameToDelete];

        let activeName = data[ZDOLA_STORAGE_KEYS.activeProfileName];
        if (activeName === profileNameToDelete) {
          const keys = Object.keys(profiles);
          activeName = keys.length > 0 ? keys[0] : null;
        }

        await chrome.storage.local.set({
          [ZDOLA_STORAGE_KEYS.multiProfiles]: profiles,
          [ZDOLA_STORAGE_KEYS.activeProfileName]: activeName
        });

        sendResponse({ success: true, profiles, activeProfile: activeName });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    })();
    return true;
  }

  if (action === 'get_profiles') {
    (async () => {
      try {
        const tabId = message.tabId || (sender.tab ? sender.tab.id : null);
        const data = await chrome.storage.local.get([
          ZDOLA_STORAGE_KEYS.multiProfiles,
          ZDOLA_STORAGE_KEYS.activeProfileName
        ]);

        let activeName = data[ZDOLA_STORAGE_KEYS.activeProfileName] || null;
        if (tabId && tabSessionMap.has(tabId)) {
          activeName = tabSessionMap.get(tabId).profileName;
        }

        sendResponse({
          profiles: data[ZDOLA_STORAGE_KEYS.multiProfiles] || {},
          activeProfile: activeName,
          tabId
        });
      } catch (err) {
        sendResponse({ success: false, error: err.message });
      }
    })();
    return true;
  }

  // --- C. STATUS & POPUP CONFIG CONTROLLERS ---
  if (action === 'GET_STATUS' || action === 'GET_DOWNLOADER_STATUS') {
    sendResponse({
      ok: true,
      config: zdolaConfig,
      history: zdolaDownloadHistory,
      totalDownloaded: zdolaConfig.totalDownloaded || 0
    });
    return false;
  }

  if (action === 'TOGGLE_AUTO_DOWNLOAD') {
    zdolaConfig.autoDownload = Boolean(message.enabled);
    saveDownloadState().then(() => {
      updateBadge();
      sendResponse({ ok: true, autoDownload: zdolaConfig.autoDownload });
    });
    return true;
  }

  if (action === 'UPDATE_CONFIG' || action === 'UPDATE_DOWNLOADER_CONFIG') {
    zdolaConfig = { ...zdolaConfig, ...(message.config || {}) };
    saveDownloadState().then(() => {
      updateBadge();
      sendResponse({ ok: true, config: zdolaConfig });
    });
    return true;
  }

  if (action === 'CLEAR_HISTORY' || action === 'CLEAR_DOWNLOAD_HISTORY') {
    zdolaDownloadHistory = [];
    saveDownloadState().then(() => {
      updateBadge();
      sendResponse({ ok: true });
    });
    return true;
  }

  if (action === 'SHOW_DOWNLOAD_ITEM') {
    if (message.downloadId) {
      chrome.downloads.show(message.downloadId);
      sendResponse({ ok: true });
    } else {
      chrome.downloads.showDefaultFolder();
      sendResponse({ ok: true });
    }
    return false;
  }

  // --- D. BATCH QUEUE & SMART QUOTA CONTROLLERS ---
  if (action === 'DOLA_QUOTA_DETECTED') {
    const tabId = sender.tab ? sender.tab.id : null;
    handleQuotaDetected(tabId, message.reason).then(() => {
      sendResponse({ ok: true, handled: true });
    });
    return true;
  }

  if (action === 'START_BATCH_QUEUE') {
    (async () => {
      try {
        const rawPrompts = Array.isArray(message.prompts) ? message.prompts : [];
        const cleanPrompts = rawPrompts.map(p => String(p).trim()).filter(p => p.length > 0);

        if (cleanPrompts.length === 0) {
          return sendResponse({ ok: false, error: 'Danh sách prompt trống!' });
        }

        const queueState = {
          prompts: cleanPrompts,
          currentIndex: 0,
          status: 'running',
          cooldownSec: parseInt(message.cooldownSec, 10) || 8,
          activeRatio: message.ratio || '9:16',
          startedAt: Date.now()
        };

        await chrome.storage.local.set({ zdola_batch_queue: queueState });

        // Dispatch first prompt
        console.log(`[ZDola Batch Queue] 🚀 Starting Batch Queue with ${cleanPrompts.length} prompts...`);
        dispatchPromptToDolaTab(cleanPrompts[0], queueState.activeRatio);

        sendResponse({ ok: true, success: true, count: cleanPrompts.length });
      } catch (err) {
        sendResponse({ ok: false, error: err.message });
      }
    })();
    return true;
  }

  if (action === 'PAUSE_BATCH_QUEUE') {
    (async () => {
      const store = await chrome.storage.local.get(['zdola_batch_queue']);
      if (store.zdola_batch_queue) {
        store.zdola_batch_queue.status = 'paused';
        await chrome.storage.local.set({ zdola_batch_queue: store.zdola_batch_queue });
      }
      sendResponse({ ok: true, status: 'paused' });
    })();
    return true;
  }

  if (action === 'RESUME_BATCH_QUEUE') {
    (async () => {
      const store = await chrome.storage.local.get(['zdola_batch_queue']);
      const queue = store.zdola_batch_queue;
      if (queue && queue.prompts && queue.currentIndex < queue.prompts.length) {
        queue.status = 'running';
        await chrome.storage.local.set({ zdola_batch_queue: queue });
        dispatchPromptToDolaTab(queue.prompts[queue.currentIndex], queue.activeRatio || '9:16');
        sendResponse({ ok: true, status: 'running', currentIndex: queue.currentIndex });
      } else {
        sendResponse({ ok: false, error: 'Không thể tiếp tục (hàng đợi rỗng hoặc đã xong)' });
      }
    })();
    return true;
  }

  if (action === 'STOP_BATCH_QUEUE') {
    (async () => {
      const store = await chrome.storage.local.get(['zdola_batch_queue']);
      if (store.zdola_batch_queue) {
        store.zdola_batch_queue.status = 'stopped';
        await chrome.storage.local.set({ zdola_batch_queue: store.zdola_batch_queue });
      }
      sendResponse({ ok: true, status: 'stopped' });
    })();
    return true;
  }

  if (action === 'GET_BATCH_QUEUE_STATUS') {
    chrome.storage.local.get(['zdola_batch_queue'], (store) => {
      sendResponse({
        ok: true,
        queue: store.zdola_batch_queue || {
          prompts: [],
          currentIndex: 0,
          status: 'idle',
          cooldownSec: 8
        }
      });
    });
    return true;
  }

  return false;
});

/* =========================================================================
   9. LIFECYCLE INITIALIZATION
   ========================================================================= */

chrome.runtime.onInstalled.addListener(async () => {
  console.log('[ZDola 30s By KHAN] Extension Installed / Updated.');
  await loadFullState();
  updateBadge();
  autoInjectIntoExistingTabs();
});

chrome.runtime.onStartup.addListener(async () => {
  console.log('[ZDola 30s By KHAN] Chrome Browser Started.');
  await loadFullState();
  updateBadge();
  autoInjectIntoExistingTabs();
});

// Immediate boot on Service Worker wake-up
loadFullState().then(autoInjectIntoExistingTabs);
