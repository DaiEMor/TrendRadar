/** Local persistence bridge for the self-hosted TrendRadar configuration editor. */

let localConfigToken = null;
let localConfigRevision = null;
let localSaveInFlight = false;

function setLocalConfigStatus(message, tone = 'normal') {
    const status = document.getElementById('local-config-status');
    if (!status) return;
    status.textContent = message;
    status.className = tone === 'error'
        ? 'text-red-600'
        : tone === 'saved'
        ? 'text-emerald-600 font-medium'
        : '';
}

function applyServerFiles(payload) {
    const files = payload.files || {};
    const configContent = files.config?.content;
    const frequencyContent = files.frequency?.content;
    const timelineContent = files.timeline?.content;
    if (![configContent, frequencyContent, timelineContent].every(value => typeof value === 'string')) {
        throw new Error('服务器返回的配置不完整');
    }

    currentYaml = configContent;
    currentFrequency = frequencyContent;
    currentTimeline = timelineContent;
    currentFrequencyData = null;

    document.getElementById('yaml-editor').value = currentYaml;
    document.getElementById('frequency-editor').value = currentFrequency;
    document.getElementById('timeline-editor').value = currentTimeline;

    updateBackdrop('yaml-editor', 'yaml-backdrop');
    updateBackdrop('frequency-editor', 'frequency-backdrop');
    updateBackdrop('timeline-editor', 'timeline-backdrop');
    syncYamlToUI();
    syncFrequencyToUI();
    syncTimelineToUI();

    localStorage.setItem(STORAGE_KEY_CONFIG, currentYaml);
    localStorage.setItem(STORAGE_KEY_FREQUENCY, currentFrequency);
    localStorage.setItem(STORAGE_KEY_TIMELINE, currentTimeline);
    localStorage.setItem(STORAGE_KEY_CONFIG_TIME, files.config.modified_at);
    localStorage.setItem(STORAGE_KEY_FREQUENCY_TIME, files.frequency.modified_at);
    localStorage.setItem(STORAGE_KEY_TIMELINE_TIME, files.timeline.modified_at);
    updateSaveTimeDisplay();
}

async function loadConfigFromServer() {
    const saveButton = document.getElementById('local-save-btn');
    if (saveButton) saveButton.disabled = true;
    setLocalConfigStatus('正在读取本机配置…');

    try {
        const response = await fetch('/api/config', { cache: 'no-store' });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
            throw new Error(payload.error || `HTTP ${response.status}`);
        }
        localConfigToken = payload.token;
        localConfigRevision = payload.revision;
        applyServerFiles(payload);
        setLocalConfigStatus('已加载当前运行配置', 'saved');
        showToast('已加载本机当前配置', 'success');
    } catch (error) {
        console.error('加载本机配置失败:', error);
        setLocalConfigStatus(`加载失败：${error.message}`, 'error');
        showToast(`加载本机配置失败：${error.message}`, 'error');
    } finally {
        if (saveButton) saveButton.disabled = !localConfigToken;
    }
}

async function saveAllToServer() {
    if (localSaveInFlight) return;
    if (!localConfigToken || !localConfigRevision) {
        showToast('本机配置尚未加载，请刷新页面后重试', 'error');
        return;
    }

    try {
        const configDocument = jsyaml.load(currentYaml);
        const timelineDocument = jsyaml.load(currentTimeline);
        if (!configDocument || typeof configDocument !== 'object' || Array.isArray(configDocument)) {
            throw new Error('config.yaml 顶层必须是 YAML 映射');
        }
        if (!timelineDocument || typeof timelineDocument !== 'object' || Array.isArray(timelineDocument)) {
            throw new Error('timeline.yaml 顶层必须是 YAML 映射');
        }
        if (!currentFrequency.trim()) {
            throw new Error('frequency_words.txt 不能为空');
        }
    } catch (error) {
        showToast(`保存前校验失败：${error.message}`, 'error');
        return;
    }

    localSaveInFlight = true;
    const saveButton = document.getElementById('local-save-btn');
    const originalHtml = saveButton?.innerHTML;
    if (saveButton) {
        saveButton.disabled = true;
        saveButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin mr-1.5"></i><span>正在保存…</span>';
    }
    setLocalConfigStatus('正在校验并保存…');

    try {
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Config-Token': localConfigToken,
            },
            body: JSON.stringify({
                revision: localConfigRevision,
                files: {
                    config: currentYaml,
                    frequency: currentFrequency,
                    timeline: currentTimeline,
                },
            }),
        });
        const payload = await response.json();
        if (!response.ok || !payload.ok) {
            throw new Error(payload.error || `HTTP ${response.status}`);
        }

        localConfigRevision = payload.revision;
        saveAllToLocalStorage();
        const savedAt = payload.saved_at || new Date().toISOString();
        localStorage.setItem(STORAGE_KEY_CONFIG_TIME, savedAt);
        localStorage.setItem(STORAGE_KEY_FREQUENCY_TIME, savedAt);
        localStorage.setItem(STORAGE_KEY_TIMELINE_TIME, savedAt);
        updateSaveTimeDisplay();
        setLocalConfigStatus('配置已保存并应用', 'saved');
        showToast('配置已写入本机，下次采集将直接使用', 'success');
    } catch (error) {
        console.error('保存本机配置失败:', error);
        setLocalConfigStatus(`保存失败：${error.message}`, 'error');
        showToast(`保存失败：${error.message}`, 'error');
    } finally {
        localSaveInFlight = false;
        if (saveButton) {
            saveButton.disabled = false;
            saveButton.innerHTML = originalHtml;
        }
    }
}

window.saveAllToServer = saveAllToServer;

document.addEventListener('DOMContentLoaded', () => {
    loadConfigFromServer();
    document.addEventListener('keydown', event => {
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 's') {
            event.preventDefault();
            saveAllToServer();
        }
    });
});
