let pollInterval = null;
let currentTopNodes = [];

document.addEventListener('DOMContentLoaded', () => {
    initApp();
});

async function initApp() {
    setupEventListeners();
    await fetchTopNodes();
    await checkScanStatus();

    // Start status polling
    pollInterval = setInterval(checkScanStatus, 3000);
}

function setupEventListeners() {
    const btnScan = document.getElementById('btnScan');
    if (btnScan) {
        btnScan.addEventListener('click', startScan);
    }

    const btnCopyAll = document.getElementById('btnCopyAll');
    if (btnCopyAll) {
        btnCopyAll.addEventListener('click', copyAllConfigs);
    }

    const btnCopySub = document.getElementById('btnCopySub');
    if (btnCopySub) {
        btnCopySub.addEventListener('click', () => {
            const subInput = document.getElementById('subUrlInput');
            if (subInput) copyToClipboard(subInput.value, 'Subscription URL copied to clipboard!');
        });
    }

    const modalClose = document.getElementById('modalClose');
    if (modalClose) {
        modalClose.addEventListener('click', closeModal);
    }

    const modalOverlay = document.getElementById('modalOverlay');
    if (modalOverlay) {
        modalOverlay.addEventListener('click', (e) => {
            if (e.target === modalOverlay) closeModal();
        });
    }
}

async function fetchTopNodes() {
    try {
        const response = await fetch('/api/vless/top?limit=5');
        const data = await response.json();
        if (data.status === 'success') {
            currentTopNodes = data.nodes || [];
            renderTopNodes(currentTopNodes);
            updateStatsHeader(data);
        }
    } catch (err) {
        console.error('Failed to fetch top nodes:', err);
    }
}

async function checkScanStatus() {
    try {
        const response = await fetch('/api/scan/status');
        const status = await response.json();
        updateScannerUI(status);
    } catch (err) {
        console.error('Failed to check scan status:', err);
    }
}

async function startScan() {
    const btnScan = document.getElementById('btnScan');
    btnScan.disabled = true;
    btnScan.innerHTML = `<div class="spinner"></div> Starting...`;

    try {
        const response = await fetch('/api/scan/start', { method: 'POST' });
        const res = await response.json();
        showToast(res.message || 'Scan initiated!');
        await checkScanStatus();
    } catch (err) {
        showToast('Failed to start scan', 'error');
        btnScan.disabled = false;
        btnScan.innerHTML = `⚡ Start Scan`;
    }
}

function updateScannerUI(status) {
    const progressWidget = document.getElementById('progressWidget');
    const btnScan = document.getElementById('btnScan');
    const progressFill = document.getElementById('progressFill');
    const progressText = document.getElementById('progressText');
    const progressStage = document.getElementById('progressStage');
    const liveBadge = document.getElementById('liveBadge');

    if (status.is_scanning) {
        progressWidget.style.display = 'block';
        btnScan.disabled = true;
        btnScan.innerHTML = `<div class="spinner"></div> Scanning...`;

        const pct = status.progress || 0;
        progressFill.style.width = `${pct}%`;
        progressText.innerText = `${pct}% (${status.processed_candidates}/${status.total_candidates})`;
        progressStage.innerText = `Stage: ${status.stage.toUpperCase()}`;

        if (liveBadge) {
            liveBadge.className = 'tag-latency latency-medium';
            liveBadge.innerHTML = `<div class="spinner" style="width:12px;height:12px;"></div> Scanning in Progress`;
        }
    } else {
        progressWidget.style.display = 'none';
        btnScan.disabled = false;
        btnScan.innerHTML = `⚡ Start Scan`;

        if (liveBadge) {
            liveBadge.className = 'tag-latency latency-fast';
            liveBadge.innerHTML = `● System Ready`;
        }

        if (status.stage === 'completed' && status.progress === 100) {
            fetchTopNodes();
        }
    }

    // Update statistics display
    document.getElementById('statTotalFound').innerText = status.total_candidates || currentTopNodes.length || 0;
    document.getElementById('statWorkingCount').innerText = status.working_candidates || currentTopNodes.length || 0;
    if (status.last_scan_time) {
        const date = new Date(status.last_scan_time);
        document.getElementById('statLastScan').innerText = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    }
}

function updateStatsHeader(data) {
    if (data.nodes && data.nodes.length > 0) {
        const latencies = data.nodes.map(n => n.latency_ms).filter(l => l !== null);
        if (latencies.length > 0) {
            const avg = Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length);
            document.getElementById('statAvgLatency').innerText = `${avg} ms`;
        }
    }
}

function renderTopNodes(nodes) {
    const container = document.getElementById('nodesList');
    if (!container) return;

    if (!nodes || nodes.length === 0) {
        container.innerHTML = `
            <div class="glass-panel" style="padding: 3rem; text-align: center; color: var(--text-muted);">
                <p>No working VLESS configs available yet.</p>
                <p style="font-size:0.875rem; margin-top:0.5rem;">Click "Start Scan" to parse sources and test nodes.</p>
            </div>
        `;
        return;
    }

    const rankClasses = ['rank-1', 'rank-2', 'rank-3', 'rank-other', 'rank-other'];
    const rankBadges = ['#1', '#2', '#3', '#4', '#5'];

    container.innerHTML = nodes.map((node, index) => {
        const rankClass = rankClasses[index] || 'rank-other';
        const rankText = rankBadges[index] || `#${index + 1}`;
        const latency = node.latency_ms ? `${Math.round(node.latency_ms)} ms` : 'N/A';

        let latClass = 'latency-fast';
        if (node.latency_ms > 350) latClass = 'latency-slow';
        else if (node.latency_ms > 180) latClass = 'latency-medium';

        const network = (node.network || 'TCP').toUpperCase();
        const security = (node.security || 'NONE').toUpperCase();
        const nodeName = node.name || `VLESS Node ${index + 1}`;
        const addressPort = `${node.address}:${node.port}`;

        return `
            <div class="glass-panel node-card">
                <div class="rank-badge ${rankClass}">${rankText}</div>
                <div class="node-info-main">
                    <div class="node-name">${escapeHtml(nodeName)}</div>
                    <div class="node-server-pill">🌐 ${escapeHtml(addressPort)}</div>
                </div>
                <div class="node-meta">
                    <span class="tag-latency ${latClass}">⚡ ${latency}</span>
                    <span class="tag-transport">🔒 ${network} / ${security}</span>
                </div>
                <div style="font-size:0.8125rem; color:var(--text-muted);">
                    ${node.stable ? '✅ 100% Stable' : '⚠️ Variable'}
                </div>
                <div class="node-actions">
                    <button class="btn btn-primary" onclick="copyNodeConfig(${index})" title="Copy VLESS Link">
                        📋 Copy
                    </button>
                    <button class="btn btn-secondary" onclick="openQrModal(${index})" title="Show QR Code">
                        📱 QR
                    </button>
                </div>
            </div>
        `;
    }).join('');

    // Update Subscription URL input
    const subUrlInput = document.getElementById('subUrlInput');
    if (subUrlInput) {
        subUrlInput.value = `${window.location.origin}/api/vless/sub`;
    }
}

function copyNodeConfig(index) {
    if (currentTopNodes[index] && currentTopNodes[index].original) {
        copyToClipboard(currentTopNodes[index].original, `Copied Config #${index + 1} to clipboard!`);
    } else {
        showToast('No config available to copy', 'error');
        console.error('Node data:', currentTopNodes[index]);
    }
}

function copyAllConfigs() {
    if (currentTopNodes.length === 0) {
        showToast('No configs to copy', 'error');
        return;
    }
    const allText = currentTopNodes.map(n => n.original).join('\n');
    copyToClipboard(allText, 'Copied all Top 5 VLESS configs!');
}

function openQrModal(index) {
    const node = currentTopNodes[index];
    if (!node || !node.original) return;

    const modal = document.getElementById('modalOverlay');
    const modalTitle = document.getElementById('modalTitle');
    const qrContainer = document.getElementById('qrCanvasContainer');

    modalTitle.innerText = `Config #${index + 1} - QR Code`;
    qrContainer.innerHTML = '';

    if (window.QRCode) {
        new QRCode(qrContainer, {
            text: node.original,
            width: 220,
            height: 220,
            colorDark: "#000000",
            colorLight: "#ffffff",
            correctLevel: QRCode.CorrectLevel.M
        });
    } else {
        const encodedUrl = encodeURIComponent(node.original);
        const qrImg = document.createElement('img');
        qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&data=${encodedUrl}`;
        qrImg.alt = 'VLESS QR Code';
        qrContainer.appendChild(qrImg);
    }

    modal.classList.add('active');
}

function closeModal() {
    const modal = document.getElementById('modalOverlay');
    if (modal) modal.classList.remove('active');
}

function copyToClipboard(text, message = 'Copied to clipboard!') {
    // Check if Clipboard API is available
    if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(() => {
            showToast(message);
        }).catch(() => {
            // Fallback if API fails
            fallbackCopy(text, message);
        });
    } else {
        // Fallback for browsers without Clipboard API
        fallbackCopy(text, message);
    }
}

function fallbackCopy(text, message) {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.left = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    try {
        document.execCommand('copy');
        showToast(message);
    } catch (err) {
        console.error('Fallback copy failed:', err);
        showToast('Failed to copy to clipboard', 'error');
    }
    document.body.removeChild(textarea);
}

function showToast(message, type = 'success') {
    const container = document.getElementById('toastContainer');
    if (!container) return;

    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.style.borderColor = type === 'error' ? 'var(--accent-rose)' : 'var(--accent-cyan)';
    toast.innerHTML = `${type === 'error' ? '❌' : '✨'} <span>${escapeHtml(message)}</span>`;

    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

function escapeHtml(str) {
    if (!str) return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}
