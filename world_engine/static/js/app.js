// ── Helpers ──
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);
const api = async (url, opts) => {
    try {
        const r = await fetch(url, opts);
        if (!r.ok) {
            const text = await r.text();
            throw new Error(`API ${r.status}: ${text}`);
        }
        return r.json();
    } catch (e) {
        console.error('API error:', url, e);
        throw e;
    }
};

function toast(msg) {
    const t = $('#toast');
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(() => t.classList.remove('show'), 2000);
}

function escHtml(s) {
    if (!s) return '';
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function autoResizeTa(el) { el.style.height = '0'; el.style.height = el.scrollHeight + 'px'; }

function toggleCollapse(header) {
    const body = header.nextElementSibling;
    const icon = header.querySelector('.icon');
    body.classList.toggle('open');
    icon.textContent = body.classList.contains('open') ? '▼' : '▶';
}

function getDayOfWeek(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][d.getDay()] || '';
}

// ── Dirty tracking ──
let _dirty = false;
function markDirty() {
    _dirty = true;
    const btn = $('#globalSaveBtn');
    if (btn) btn.classList.add('visible');
}
function clearDirty() {
    _dirty = false;
    const btn = $('#globalSaveBtn');
    if (btn) btn.classList.remove('visible');
}
async function globalSave() {
    try {
        const tab = document.querySelector('.tab-btn.active')?.dataset.tab;
        if (_archiveMode === 'archive' && _currentArchiveId) {
            await archiveSaveEdits();
        } else {
            if (tab === 'settings') await saveSettings();
            else if (tab === 'map') await mapSave();
            else if (tab === 'characters') await saveAllChars();
            else if (tab === 'world-settings') await saveWorldSettings();
        }
        clearDirty();
    } catch (e) {
        console.error('Save error:', e);
        toast('❌ 保存失败: ' + e.message);
    }
}

// ── Tab switching ──
function switchTab(tabName) {
    $$('.tab-btn').forEach(b => b.classList.remove('active'));
    $$('.tab-content').forEach(c => c.classList.remove('active'));
    const btn = document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
    if (btn) btn.classList.add('active');
    const content = $(`#tab-${tabName}`);
    if (content) content.classList.add('active');
    localStorage.setItem('we_active_tab', tabName);
    clearDirty();
    // 切到角色标签时刷新位置信息
    if (tabName === 'characters' && typeof _archiveMode !== 'undefined' && _archiveMode === 'current') {
        api('/api/state').then(s => { STATE = s; renderCharsUI(); });
    }
}
$$('.tab-btn').forEach(btn => {
    btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

// ── Data cache ──
let STATE = {}, CONFIG = {};

// ── Init ──
async function loadAllTabs() {
    await Promise.all([
        loadSessions(),
        loadEvents(),
        loadCharacters(),
        loadMap(),
        loadWorldSettings(),
    ]);
}

async function init() {
    STATE = await api('/api/state');
    CONFIG = await api('/api/config');
    let timeStr = (STATE.current_time || '').replace('T', ' ').replace(/:\d{2}$/, '');
    let dow = getDayOfWeek(STATE.current_time);
    $('#headerTime').textContent = `🕐 ${timeStr} ${dow}`;
    loadSettings();

    // 恢复存档模式（先判断再加载，避免闪烁）
    const savedMode = localStorage.getItem('we_archive_mode');
    const savedArchiveId = localStorage.getItem('we_archive_id');
    if (savedMode === 'archive') {
        _currentArchiveId = savedArchiveId;
        await switchArchiveMode('archive');
    } else {
        await loadAllTabs();
        startAutoRefresh();
    }

    // 恢复标签页（必须在所有脚本加载完毕、数据就绪后执行）
    const savedTab = localStorage.getItem('we_active_tab');
    if (savedTab && $(`#tab-${savedTab}`)) switchTab(savedTab);

    // 恢复滚动位置（等内容渲染完毕后）
    requestAnimationFrame(() => {
        setTimeout(() => {
            const savedScroll = sessionStorage.getItem('we_scroll_y');
            if (savedScroll) window.scrollTo(0, parseInt(savedScroll));
            const savedDetail = sessionStorage.getItem('we_char_detail_scroll');
            const savedSidebar = sessionStorage.getItem('we_char_sidebar_scroll');
            const detail = document.querySelector('.char-detail');
            const sidebar = document.querySelector('.char-sidebar');
            if (detail && savedDetail) detail.scrollTop = parseInt(savedDetail);
            if (sidebar && savedSidebar) sidebar.scrollTop = parseInt(savedSidebar);
        }, 50);
    });
}

// 页面卸载前保存滚动位置
window.addEventListener('beforeunload', () => {
    sessionStorage.setItem('we_scroll_y', window.scrollY);
    const detail = document.querySelector('.char-detail');
    const sidebar = document.querySelector('.char-sidebar');
    if (detail) sessionStorage.setItem('we_char_detail_scroll', detail.scrollTop);
    if (sidebar) sessionStorage.setItem('we_char_sidebar_scroll', sidebar.scrollTop);
});

init();
