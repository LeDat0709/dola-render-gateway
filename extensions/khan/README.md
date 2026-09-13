# ⚡ Khan zDola30s Pro v1.1 — 30s AI Video Generator & Auto-Downloader

**Ultra-Fast 30s Video Prompt Executor & Automatic Unwatermarked 1080P Downloader for Dola AI (Seedance 2.5) by Khan.**

---

## 🌟 Key Features

1. **⚡ 30s HD Duration Bypass Engine**:
   - Universal payload override locking generated video duration directly to 30s HD VIP quality.
   - Zero refusal prompt execution with Seedance 2.5 architecture.

2. **🚀 1-Click Prompt to Video Generation**:
   - Clean single-screen interface: Paste prompt, select aspect ratio (9:16, 16:9, 1:1, etc.), and click **"Generate 30s Video & Auto-Download"**.
   - Automatically opens or switches to Dola AI, fills composer, and triggers generation.

3. **🛡️ 100% Unwatermarked Raw 1080P Auto-Download**:
   - Intercepts raw master MP4 CDN video streams the exact second video rendering finishes.
   - Automatically saves unwatermarked videos directly into your chosen downloads folder (default: `ZDola_Videos`).

4. **⚡ On-Demand Screen Video Grabber**:
   - 1-Click button to instantly grab and download any video currently displayed on Dola without watermarks.

5. **👥 Multi-Account Isolated Session Manager**:
   - Save and manage multiple Dola accounts directly from the extension popup.
   - Per-tab cookie isolation powered by Chrome DeclarativeNetRequest (DNR).
   - Run multiple accounts simultaneously in different tabs without cookie collisions.

6. **🚀 Batch Auto-Prompting Queue & Smart Quota Rotator**:
   - Paste 10 - 50 prompts or import directly from `.txt` / `.csv` files.
   - Automatic execution: generates and downloads video for Prompt #1, waits cooldown (5-10s), then automatically executes Prompt #2.
   - **Smart Quota Auto-Rotation**: When an account reaches its daily limit (429 / quota exceeded), the extension automatically rotates to your next saved account, reloads the tab, and continues the batch queue without manual intervention!

---

## 📂 Project Structure

```
├── manifest.json       # Manifest V3 extension configuration for zDola30s Pro
├── background.js      # Background service worker & auto-download file manager
├── content.js         # Content script bridge connecting Dola page to background
├── extractor.js       # In-page media extractor & raw unwatermarked stream capture
├── inject.js          # Main-world duration bypass engine & network hooks
├── popup.html         # Ultra-clean glassmorphic dark interface
├── popup.css          # Modern cyberpunk/neon dark theme stylesheet
├── popup.js           # 1-Click prompt automation & history controller
├── icon16.png         # Toolbar icon (16x16)
├── icon48.png         # Extension page icon (48x48)
└── icon128.png        # High-resolution icon (128x128)
```

---

## 🚀 How to Install / Load in Chrome

1. Open Google Chrome and go to `chrome://extensions/`.
2. Turn ON **Developer mode** (top-right corner).
3. Click **Load unpacked** and select this extension folder (`Dola 30S`).
4. Click the extension icon in Chrome toolbar, paste your prompt, and click **🚀 GENERATE 30S VIDEO & AUTO-DOWNLOAD**!
