// Dola 30 Sec By KHAN - Clean Single-Screen Engine
document.addEventListener('DOMContentLoaded', () => {
    // Elements
    const promptInput = document.getElementById('prompt-input');
    const charCount = document.getElementById('char-count');
    const btnPaste = document.getElementById('btn-paste');
    const btnClear = document.getElementById('btn-clear');
    const btnSample = document.getElementById('btn-sample');
    const btnGenerate = document.getElementById('btn-generate');
    const generateBtnText = document.getElementById('generate-btn-text');
    const ratioBtns = document.querySelectorAll('.ratio-btn');
    const toggleAutoDownload = document.getElementById('toggle-auto-download');
    const actionAlert = document.getElementById('action-alert');
    const alertMessage = document.getElementById('alert-message');
    const connectionStatus = document.getElementById('connection-status');
    const downloadsList = document.getElementById('downloads-list');
    const downloadCountBadge = document.getElementById('download-count-badge');

    // Info Modal Elements
    const btnInfoToggle = document.getElementById('btn-info-toggle');
    const btnInfoClose = document.getElementById('btn-info-close');
    const infoModal = document.getElementById('info-modal');

    // Multi-Account Elements
    const profileSelect = document.getElementById('profile-select');
    const profileColorDot = document.getElementById('profile-color-dot');
    const btnSaveCurrentProfile = document.getElementById('btn-save-current-profile');
    const btnImportCookieModal = document.getElementById('btn-import-cookie-modal');
    const btnRotateProfile = document.getElementById('btn-rotate-profile');
    const btnOpenTabProfile = document.getElementById('btn-open-tab-profile');
    const btnDeleteProfile = document.getElementById('btn-delete-profile');

    const importCookieModal = document.getElementById('import-cookie-modal');
    const btnImportClose = document.getElementById('btn-import-close');
    const importProfileName = document.getElementById('import-profile-name');
    const importCookieInput = document.getElementById('import-cookie-input');
    const btnSubmitImport = document.getElementById('btn-submit-import');

    let activeRatio = '9:16';
    let isAutoDownload = true;
    let recentDownloads = [];
    let savedProfiles = {};
    let activeProfileName = '';

    // Load and render Multi-Account Profiles
    function loadProfiles() {
        chrome.runtime.sendMessage({ action: 'get_profiles' }, (res) => {
            if (chrome.runtime.lastError || !res) return;
            savedProfiles = res.profiles || {};
            activeProfileName = res.activeProfile || '';
            renderProfilesDropdown();
        });
    }

    function renderProfilesDropdown() {
        if (!profileSelect) return;
        profileSelect.innerHTML = '';
        const names = Object.keys(savedProfiles);

        if (names.length === 0) {
            const opt = document.createElement('option');
            opt.value = '';
            opt.textContent = '(Chưa lưu nick nào - Hãy bấm Lưu Nick Này)';
            profileSelect.appendChild(opt);
            if (profileColorDot) profileColorDot.style.background = '#6b7280';
            return;
        }

        names.forEach(name => {
            const prof = savedProfiles[name];
            const opt = document.createElement('option');
            opt.value = name;
            opt.textContent = `${name} (${prof.cookieCount || prof.cookies?.length || 0} cookies)`;
            if (name === activeProfileName) {
                opt.selected = true;
            }
            profileSelect.appendChild(opt);
        });

        const current = savedProfiles[activeProfileName] || savedProfiles[names[0]];
        if (current && profileColorDot) {
            profileColorDot.style.background = current.color || '#8b5cf6';
        }
    }

    // Modal Event Listeners
    if (btnInfoToggle && infoModal) {
        btnInfoToggle.addEventListener('click', () => {
            infoModal.style.display = 'flex';
        });
    }

    if (btnInfoClose && infoModal) {
        btnInfoClose.addEventListener('click', () => {
            infoModal.style.display = 'none';
        });
    }

    if (infoModal) {
        infoModal.addEventListener('click', (e) => {
            if (e.target === infoModal) {
                infoModal.style.display = 'none';
            }
        });
    }

    // Import Cookie Modal Listeners
    if (btnImportCookieModal && importCookieModal) {
        btnImportCookieModal.addEventListener('click', () => {
            importCookieModal.style.display = 'flex';
            importProfileName.value = `Nick_${Object.keys(savedProfiles).length + 1}`;
            importCookieInput.value = '';
            importCookieInput.focus();
        });
    }

    if (btnImportClose && importCookieModal) {
        btnImportClose.addEventListener('click', () => {
            importCookieModal.style.display = 'none';
        });
    }

    if (importCookieModal) {
        importCookieModal.addEventListener('click', (e) => {
            if (e.target === importCookieModal) {
                importCookieModal.style.display = 'none';
            }
        });
    }

    // Submit Import Cookies
    if (btnSubmitImport) {
        btnSubmitImport.addEventListener('click', () => {
            const name = (importProfileName.value || '').trim() || `Nick_${Date.now().toString().slice(-4)}`;
            const cookieStr = (importCookieInput.value || '').trim();

            if (!cookieStr) {
                showAlert('Vui lòng dán chuỗi cookie hợp lệ!', 'error');
                return;
            }

            chrome.runtime.sendMessage({
                action: 'import_profile_cookies',
                profileName: name,
                cookieString: cookieStr
            }, (res) => {
                if (res?.success) {
                    importCookieModal.style.display = 'none';
                    showAlert(`Đã nhập thành công tài khoản [${name}]!`, 'success');
                    loadProfiles();
                } else {
                    showAlert(`Lỗi: ${res?.error || 'Không phân tích được cookie'}`, 'error');
                }
            });
        });
    }

    // Save Current Tab Account
    if (btnSaveCurrentProfile) {
        btnSaveCurrentProfile.addEventListener('click', () => {
            const defaultName = `Acc_${Object.keys(savedProfiles).length + 1}`;
            const profileName = prompt('Nhập tên cho tài khoản này (Ví dụ: Nick 1, VIP Dola):', defaultName);
            if (!profileName) return;

            showAlert('Đang trích xuất cookie từ tab hiện tại...', 'info');
            chrome.runtime.sendMessage({
                action: 'save_profile_from_browser',
                profileName: profileName.trim()
            }, (res) => {
                if (res?.success) {
                    showAlert(`Đã lưu tài khoản [${profileName}] (${res.cookieCount} cookies)!`, 'success');
                    loadProfiles();
                } else {
                    showAlert(`Lỗi lưu tài khoản: ${res?.error || 'Chưa đăng nhập Dola'}`, 'error');
                }
            });
        });
    }

    // Switch profile on dropdown change
    if (profileSelect) {
        profileSelect.addEventListener('change', () => {
            const selectedName = profileSelect.value;
            if (!selectedName) return;

            showAlert(`Đang chuyển sang [${selectedName}]...`, 'info');
            chrome.runtime.sendMessage({
                action: 'switch_profile',
                profileName: selectedName
            }, (res) => {
                if (res?.success) {
                    activeProfileName = selectedName;
                    const prof = savedProfiles[selectedName];
                    if (prof && profileColorDot) {
                        profileColorDot.style.background = prof.color || '#8b5cf6';
                    }
                    showAlert(`Đã chuyển sang tài khoản [${selectedName}]!`, 'success');
                } else {
                    showAlert(`Lỗi: ${res?.error || 'Không đổi được tài khoản'}`, 'error');
                }
            });
        });
    }

    // Open profile in new isolated tab
    if (btnOpenTabProfile) {
        btnOpenTabProfile.addEventListener('click', () => {
            const selectedName = profileSelect.value;
            if (!selectedName) {
                showAlert('Vui lòng chọn hoặc lưu một tài khoản trước!', 'error');
                return;
            }
            chrome.runtime.sendMessage({
                action: 'open_profile_new_tab',
                profileName: selectedName
            }, (res) => {
                if (res?.success) {
                    showAlert(`Đã mở tab riêng biệt cho [${selectedName}]!`, 'success');
                }
            });
        });
    }

    // Auto rotate profile (Next account)
    if (btnRotateProfile) {
        btnRotateProfile.addEventListener('click', () => {
            showAlert('Đang tự động xoay sang tài khoản tiếp theo...', 'info');
            chrome.runtime.sendMessage({ action: 'auto_rotate_profile' }, (res) => {
                if (res?.success) {
                    showAlert(`🔄 Đã xoay sang [${res.to}]!`, 'success');
                    loadProfiles();
                } else {
                    showAlert(`Không thể xoay: ${res?.reason || 'Cần ít nhất 2 tài khoản'}`, 'error');
                }
            });
        });
    }

    // Delete selected profile
    if (btnDeleteProfile) {
        btnDeleteProfile.addEventListener('click', () => {
            const selectedName = profileSelect.value;
            if (!selectedName) return;
            if (!confirm(`Bạn có chắc muốn xóa tài khoản [${selectedName}]?`)) return;

            chrome.runtime.sendMessage({
                action: 'delete_profile',
                profileName: selectedName
            }, (res) => {
                if (res?.success) {
                    showAlert(`Đã xóa tài khoản [${selectedName}]!`, 'info');
                    loadProfiles();
                }
            });
        });
    }

    // Initialize Profiles
    loadProfiles();

    // Load saved settings
    chrome.storage.local.get(['zdola_saved_ratio', 'zdola_auto_download', 'zdola_recent_downloads'], (res) => {
        if (res.zdola_saved_ratio) {
            activeRatio = res.zdola_saved_ratio;
            ratioBtns.forEach(btn => {
                btn.classList.toggle('active', btn.dataset.ratio === activeRatio);
            });
        }
        if (typeof res.zdola_auto_download === 'boolean') {
            isAutoDownload = res.zdola_auto_download;
            toggleAutoDownload.checked = isAutoDownload;
        }
        if (res.zdola_recent_downloads && Array.isArray(res.zdola_recent_downloads)) {
            recentDownloads = res.zdola_recent_downloads;
            renderDownloads();
        }
    });

    // Check active Dola tab connection
    checkDolaTab();

    // Event: Character counter
    promptInput.addEventListener('input', () => {
        charCount.textContent = `${promptInput.value.length} chars`;
    });

    // Event: Paste button
    btnPaste.addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (text) {
                promptInput.value = text;
                charCount.textContent = `${promptInput.value.length} chars`;
                showAlert('Prompt pasted from clipboard!', 'success');
            }
        } catch (e) {
            promptInput.focus();
            document.execCommand('paste');
        }
    });

    // Event: Clear button
    btnClear.addEventListener('click', () => {
        promptInput.value = '';
        charCount.textContent = '0 chars';
        promptInput.focus();
    });

    // Sample prompts
    const samples = [
        "Cinematic 8k shot of a futuristic sports car racing through neon-lit rainy Tokyo streets, ultra-realistic reflections, 30s dynamic camera movement.",
        "Hyper-realistic cinematic slow motion of a mystical dragon flying over snow-covered crystal mountains at sunrise, dramatic lighting, 8k resolution.",
        "Fashion model in iridescent avant-garde neon dress walking through cybernetic garden with glowing holographic butterflies, cinematic 30s commercial."
    ];

    btnSample.addEventListener('click', () => {
        const rand = samples[Math.floor(Math.random() * samples.length)];
        promptInput.value = rand;
        charCount.textContent = `${rand.length} chars`;
        showAlert('Loaded example prompt!', 'info');
    });

    // Event: Aspect Ratio Selection
    ratioBtns.forEach(btn => {
        btn.addEventListener('click', () => {
            ratioBtns.forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            activeRatio = btn.dataset.ratio;
            chrome.storage.local.set({ zdola_saved_ratio: activeRatio });
            
            // Broadcast ratio to Dola tab
            chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
                if (tabs && tabs[0]) {
                    chrome.tabs.sendMessage(tabs[0].id, {
                        type: 'SET_ACTIVE_RATIO',
                        ratio: activeRatio
                    }, () => {
                        if (chrome.runtime.lastError) { /* ignore */ }
                    });
                }
            });
        });
    });

    // Event: Auto download toggle
    toggleAutoDownload.addEventListener('change', () => {
        isAutoDownload = toggleAutoDownload.checked;
        chrome.storage.local.set({ zdola_auto_download: isAutoDownload });
        showAlert(isAutoDownload ? 'Auto-Download Enabled (1080P HD)' : 'Auto-Download Paused', isAutoDownload ? 'success' : 'info');
    });

    // Event: Generate Button
    btnGenerate.addEventListener('click', async () => {
        const prompt = promptInput.value.trim();
        if (!prompt) {
            showAlert('Please enter or paste a prompt first!', 'error');
            promptInput.focus();
            return;
        }

        btnGenerate.disabled = true;
        generateBtnText.textContent = 'Injecting 30s Task...';

        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            const currentTab = tabs && tabs[0];
            if (!currentTab || !currentTab.url || (!currentTab.url.includes('dola.com') && !currentTab.url.includes('dola.ai') && !currentTab.url.includes('doubao.com'))) {
                // If not on Dola tab, search for open Dola tab
                chrome.tabs.query({ url: ['*://*.dola.com/*', '*://*.dola.ai/*', '*://*.doubao.com/*'] }, (dolaTabs) => {
                    if (dolaTabs && dolaTabs.length > 0) {
                        const targetTab = dolaTabs[0];
                        chrome.tabs.update(targetTab.id, { active: true });
                        sendGenerateCommand(targetTab.id, prompt, activeRatio);
                    } else {
                        // Open new Dola tab
                        chrome.tabs.create({ url: 'https://www.dola.com' }, (newTab) => {
                            showAlert('Opening Dola... Click Generate again once loaded!', 'info');
                            btnGenerate.disabled = false;
                            generateBtnText.textContent = 'Generate 30s Video';
                        });
                    }
                });
                return;
            }

            sendGenerateCommand(currentTab.id, prompt, activeRatio);
        });
    });

    function sendGenerateCommand(tabId, prompt, ratio) {
        chrome.tabs.sendMessage(tabId, {
            type: 'INJECT_DOLA_TASK',
            prompt: prompt,
            duration: 30,
            ratio: ratio
        }, (response) => {
            btnGenerate.disabled = false;
            generateBtnText.textContent = 'Generate 30s Video';

            if (chrome.runtime.lastError || !response || !response.success) {
                // Fallback execute script
                chrome.scripting.executeScript({
                    target: { tabId: tabId },
                    func: executeDirectPromptInjection,
                    args: [prompt, ratio]
                }).then(() => {
                    showAlert('⚡ Prompt injected! 30s generation started.', 'success');
                }).catch((err) => {
                    showAlert('Please refresh the Dola tab and try again.', 'error');
                });
            } else {
                showAlert('⚡ 30s Video Generation triggered! Auto-download active.', 'success');
            }
        });
    }

    function executeDirectPromptInjection(promptText, targetRatio) {
        // Direct composer search & injection
        const textarea = document.querySelector('textarea.semi-input-textarea, textarea, div[contenteditable="true"]');
        if (textarea) {
            if (textarea.tagName === 'DIV') {
                textarea.innerText = promptText;
            } else {
                textarea.value = promptText;
            }
            textarea.dispatchEvent(new Event('input', { bubbles: true }));
            textarea.dispatchEvent(new Event('change', { bubbles: true }));

            // Dispatch 30s ratio update
            window.postMessage({ type: 'PURZA_UPDATE_SETTINGS', ratio: targetRatio, duration: 30 }, '*');

            // Click send button
            setTimeout(() => {
                const sendBtn = document.querySelector('button[type="submit"], div[role="button"][class*="send"], button[class*="send"], svg[class*="send"]')?.closest('button, div[role="button"]');
                if (sendBtn) {
                    sendBtn.click();
                }
            }, 300);
        }
    }

    function checkDolaTab() {
        chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
            const currentTab = tabs && tabs[0];
            const isDola = currentTab && currentTab.url && (currentTab.url.includes('dola.com') || currentTab.url.includes('dola.ai') || currentTab.url.includes('doubao.com'));
            if (isDola) {
                connectionStatus.style.background = 'rgba(16, 185, 129, 0.12)';
                connectionStatus.style.borderColor = 'rgba(16, 185, 129, 0.3)';
                connectionStatus.querySelector('.status-text').textContent = 'Dola Active';
                connectionStatus.querySelector('.status-text').style.color = '#10b981';
                connectionStatus.querySelector('.status-indicator').style.background = '#10b981';
            } else {
                connectionStatus.style.background = 'rgba(234, 179, 8, 0.12)';
                connectionStatus.style.borderColor = 'rgba(234, 179, 8, 0.3)';
                connectionStatus.querySelector('.status-text').textContent = 'Dola Tab Inactive';
                connectionStatus.querySelector('.status-text').style.color = '#eab308';
                connectionStatus.querySelector('.status-indicator').style.background = '#eab308';
            }
        });
    }

    function showAlert(msg, type = 'info') {
        actionAlert.style.display = 'flex';
        actionAlert.className = `action-alert ${type}`;
        alertMessage.textContent = msg;
        setTimeout(() => {
            actionAlert.style.display = 'none';
        }, 3500);
    }

    function renderDownloads() {
        if (!recentDownloads || recentDownloads.length === 0) {
            downloadsList.innerHTML = '<div class="empty-downloads">No videos generated in this session yet. Ready for your prompt!</div>';
            downloadCountBadge.textContent = '0 saved';
            return;
        }

        downloadCountBadge.textContent = `${recentDownloads.length} saved`;
        downloadsList.innerHTML = '';

        recentDownloads.slice(0, 5).forEach(item => {
            const div = document.createElement('div');
            div.className = 'download-item';
            div.innerHTML = `
                <span class="download-item-name" title="${item.filename || '30s Video'}">${item.filename || '30s Video'}</span>
                <span class="download-item-tag">1080P HD</span>
            `;
            downloadsList.appendChild(div);
        });
    }

    // Listen for background auto-download notifications
    chrome.runtime.onMessage.addListener((message) => {
        if (message.type === 'AUTO_DOWNLOAD_COMPLETED') {
            const newItem = {
                filename: message.filename || `Khan_30s_${Date.now()}.mp4`,
                timestamp: Date.now()
            };
            recentDownloads.unshift(newItem);
            chrome.storage.local.set({ zdola_recent_downloads: recentDownloads });
            renderDownloads();
            showAlert('🎉 1080P HD Video Auto-Downloaded!', 'success');
        }

        if (message.type === 'BATCH_QUEUE_PROGRESS') {
            updateBatchUI({
                status: 'running',
                currentIndex: message.currentIndex,
                total: message.total,
                nextPrompt: message.nextPrompt,
                cooldownSec: message.cooldownSec
            });
        }

        if (message.type === 'BATCH_QUEUE_COMPLETED') {
            updateBatchUI({
                status: 'completed',
                currentIndex: message.total,
                total: message.total
            });
            showAlert('🎉 Hoàn thành tạo hàng loạt tất cả video trong hàng đợi!', 'success');
        }
    });

    // =========================================================================
    // 🚀 BATCH QUEUE & MODE SWITCHER CONTROLLERS
    // =========================================================================
    const tabBtnSingle = document.getElementById('tab-btn-single');
    const tabBtnBatch = document.getElementById('tab-btn-batch');
    const singleModeView = document.getElementById('single-mode-view');
    const batchModeView = document.getElementById('batch-mode-view');

    const batchPromptsInput = document.getElementById('batch-prompts-input');
    const batchCountLabel = document.getElementById('batch-count-label');
    const batchCooldownInput = document.getElementById('batch-cooldown-input');
    const btnImportFile = document.getElementById('btn-import-file');
    const batchFileInput = document.getElementById('batch-file-input');
    const btnBatchSample = document.getElementById('btn-batch-sample');
    const btnBatchClear = document.getElementById('btn-batch-clear');

    const batchProgressCard = document.getElementById('batch-progress-card');
    const batchStatusText = document.getElementById('batch-status-text');
    const batchPercentText = document.getElementById('batch-percent-text');
    const batchProgressFill = document.getElementById('batch-progress-fill');
    const batchCurrentPromptPreview = document.getElementById('batch-current-prompt-preview');

    const btnStartBatch = document.getElementById('btn-start-batch');
    const batchStartBtnText = document.getElementById('batch-start-btn-text');
    const btnPauseBatch = document.getElementById('btn-pause-batch');
    const btnStopBatch = document.getElementById('btn-stop-batch');

    // Switch between Single and Batch mode
    if (tabBtnSingle && tabBtnBatch) {
        tabBtnSingle.addEventListener('click', () => {
            tabBtnSingle.classList.add('active');
            tabBtnBatch.classList.remove('active');
            singleModeView.style.display = 'block';
            batchModeView.style.display = 'none';
        });

        tabBtnBatch.addEventListener('click', () => {
            tabBtnBatch.classList.add('active');
            tabBtnSingle.classList.remove('active');
            singleModeView.style.display = 'none';
            batchModeView.style.display = 'block';
        });
    }

    function parseBatchPromptsList() {
        if (!batchPromptsInput) return [];
        const raw = batchPromptsInput.value || '';
        return raw.split(/\r?\n/)
            .map(line => line.replace(/^[\d+.)\-*•\s]+/, '').trim())
            .filter(line => line.length > 5);
    }

    function updateBatchCount() {
        const prompts = parseBatchPromptsList();
        if (batchCountLabel) {
            batchCountLabel.textContent = `${prompts.length} prompts phát hiện`;
        }
    }

    if (batchPromptsInput) {
        batchPromptsInput.addEventListener('input', updateBatchCount);
    }

    // Sample Batch Prompts
    if (btnBatchSample) {
        btnBatchSample.addEventListener('click', () => {
            batchPromptsInput.value = [
                "Cinematic 8k shot of a futuristic sports car racing through neon-lit rainy Tokyo streets, ultra-realistic reflections, 30s dynamic camera movement.",
                "Hyper-realistic slow motion of a mystical dragon flying over crystal snow mountains at sunrise, golden dramatic lighting, 8k resolution.",
                "Fashion model in iridescent avant-garde neon dress walking through cybernetic garden with glowing holographic butterflies, cinematic 30s commercial."
            ].join('\n\n');
            updateBatchCount();
            showAlert('Đã nạp 3 prompt mẫu vào hàng đợi!', 'info');
        });
    }

    if (btnBatchClear) {
        btnBatchClear.addEventListener('click', () => {
            batchPromptsInput.value = '';
            updateBatchCount();
        });
    }

    // Import from .txt or .csv file
    if (btnImportFile && batchFileInput) {
        btnImportFile.addEventListener('click', () => {
            batchFileInput.click();
        });

        batchFileInput.addEventListener('change', (e) => {
            const file = e.target.files && e.target.files[0];
            if (!file) return;

            const reader = new FileReader();
            reader.onload = (event) => {
                const content = event.target?.result;
                if (typeof content === 'string') {
                    batchPromptsInput.value = content;
                    updateBatchCount();
                    showAlert(`Đã nạp file [${file.name}] thành công!`, 'success');
                }
            };
            reader.readAsText(file);
        });
    }

    function updateBatchUI(queue) {
        if (!queue) return;
        const { status, currentIndex, total, nextPrompt, cooldownSec } = queue;

        if (status === 'running') {
            batchProgressCard.style.display = 'block';
            btnStartBatch.style.display = 'none';
            btnPauseBatch.style.display = 'block';
            btnPauseBatch.textContent = '⏸️ Tạm Dừng';
            btnStopBatch.style.display = 'block';

            const curr = (currentIndex || 0) + 1;
            const tot = total || 1;
            const percent = Math.min(100, Math.round((currentIndex / tot) * 100));

            batchStatusText.textContent = `Đang xử lý prompt ${curr} / ${tot}...`;
            batchPercentText.textContent = `${percent}%`;
            batchProgressFill.style.width = `${percent}%`;
            batchCurrentPromptPreview.textContent = nextPrompt ? `Prompt: ${nextPrompt}` : (cooldownSec ? `⏳ Đang nghỉ ${cooldownSec}s...` : 'Đang gửi tác vụ...');
        } else if (status === 'paused') {
            batchProgressCard.style.display = 'block';
            btnStartBatch.style.display = 'none';
            btnPauseBatch.style.display = 'block';
            btnPauseBatch.textContent = '▶️ Tiếp Tục';
            btnStopBatch.style.display = 'block';
            batchStatusText.textContent = `Đang tạm dừng tại prompt ${(currentIndex || 0) + 1} / ${total || 1}`;
        } else if (status === 'completed') {
            batchProgressCard.style.display = 'block';
            btnStartBatch.style.display = 'block';
            batchStartBtnText.textContent = 'Bắt Đầu Lại Hàng Đợi';
            btnPauseBatch.style.display = 'none';
            btnStopBatch.style.display = 'none';

            batchStatusText.textContent = `🎉 Đã hoàn tất cả ${total} video!`;
            batchPercentText.textContent = '100%';
            batchProgressFill.style.width = '100%';
            batchCurrentPromptPreview.textContent = 'Tất cả video 1080P raw đã tải về máy thành công.';
        } else {
            // Idle or stopped
            batchProgressCard.style.display = 'none';
            btnStartBatch.style.display = 'block';
            batchStartBtnText.textContent = 'Bắt Đầu Tạo Hàng Loạt';
            btnPauseBatch.style.display = 'none';
            btnStopBatch.style.display = 'none';
        }
    }

    // Start Batch Queue
    if (btnStartBatch) {
        btnStartBatch.addEventListener('click', () => {
            const prompts = parseBatchPromptsList();
            if (prompts.length === 0) {
                showAlert('Vui lòng dán ít nhất 1 prompt vào danh sách!', 'error');
                batchPromptsInput.focus();
                return;
            }

            const cooldown = parseInt(batchCooldownInput.value, 10) || 8;
            showAlert(`🚀 Bắt đầu hàng đợi ${prompts.length} video (nghỉ ${cooldown}s giữa các clip)...`, 'success');

            chrome.runtime.sendMessage({
                action: 'START_BATCH_QUEUE',
                prompts,
                cooldownSec: cooldown,
                ratio: activeRatio
            }, (res) => {
                if (res?.ok) {
                    updateBatchUI({
                        status: 'running',
                        currentIndex: 0,
                        total: prompts.length,
                        nextPrompt: prompts[0]
                    });
                } else {
                    showAlert(`Lỗi: ${res?.error || 'Không thể bắt đầu'}`, 'error');
                }
            });
        });
    }

    // Pause / Resume Batch Queue
    if (btnPauseBatch) {
        btnPauseBatch.addEventListener('click', () => {
            if (btnPauseBatch.textContent.includes('Tạm Dừng')) {
                chrome.runtime.sendMessage({ action: 'PAUSE_BATCH_QUEUE' }, () => {
                    btnPauseBatch.textContent = '▶️ Tiếp Tục';
                    showAlert('Hàng đợi đã được tạm dừng.', 'info');
                });
            } else {
                chrome.runtime.sendMessage({ action: 'RESUME_BATCH_QUEUE' }, (res) => {
                    if (res?.ok) {
                        btnPauseBatch.textContent = '⏸️ Tạm Dừng';
                        showAlert('Tiếp tục chạy hàng đợi...', 'success');
                    } else {
                        showAlert(`Lỗi: ${res?.error || 'Không tiếp tục được'}`, 'error');
                    }
                });
            }
        });
    }

    // Stop Batch Queue
    if (btnStopBatch) {
        btnStopBatch.addEventListener('click', () => {
            if (!confirm('Bạn có chắc muốn dừng và hủy hàng đợi này?')) return;
            chrome.runtime.sendMessage({ action: 'STOP_BATCH_QUEUE' }, () => {
                updateBatchUI({ status: 'stopped' });
                showAlert('Đã dừng hàng đợi video.', 'info');
            });
        });
    }

    // Check ongoing batch queue on load
    chrome.runtime.sendMessage({ action: 'GET_BATCH_QUEUE_STATUS' }, (res) => {
        if (res?.ok && res.queue) {
            if (res.queue.status === 'running' || res.queue.status === 'paused') {
                if (tabBtnBatch && tabBtnSingle) {
                    tabBtnBatch.classList.add('active');
                    tabBtnSingle.classList.remove('active');
                    singleModeView.style.display = 'none';
                    batchModeView.style.display = 'block';
                }
                updateBatchUI(res.queue);
            }
        }
    });
});
