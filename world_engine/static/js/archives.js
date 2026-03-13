// ── Archive mode ──
let _archiveMode = 'current';
let _archiveList = [];
let _currentArchiveId = null;
let _archiveData = null;

async function switchArchiveMode(mode) {
    _archiveMode = mode;
    localStorage.setItem('we_archive_mode', mode);
    $('#togCurrent').className = 'mode-label' + (mode === 'current' ? ' active' : '');
    $('#togArchive').className = 'mode-label' + (mode === 'archive' ? ' active' : '');
    $('#archiveBar').style.display = mode === 'archive' ? 'flex' : 'none';
    clearDirty();
    if (mode === 'current') {
        // 重新获取当前数据的 STATE，避免残留存档数据
        STATE = await api('/api/state');
        let timeStr = (STATE.current_time || '').replace('T', ' ').replace(/:\d{2}$/, '');
        let dow = getDayOfWeek(STATE.current_time);
        $('#headerTime').textContent = `🕐 ${timeStr} ${dow}`;
        loadAllTabs();
        startAutoRefresh();
    } else {
        stopAutoRefresh();
        loadArchiveList();
    }
}

async function loadArchiveList() {
    _archiveList = await api('/api/archives/list');
    const sel = $('#archiveSelect');
    if (_archiveList.length === 0) {
        sel.innerHTML = '<option value="">— 暂无存档 —</option>';
        _currentArchiveId = null;
        $('#tab-sessions').innerHTML = '<div class="empty">请先创建或选择存档</div>';
        $('#tab-events').innerHTML = '';
        $('#tab-characters').innerHTML = '';
        $('#tab-map').innerHTML = '';
    } else {
        sel.innerHTML = _archiveList.map(a =>
            '<option value="' + a.id + '"' + (a.id === _currentArchiveId ? ' selected' : '') + '>' + escHtml(a.name) + '</option>'
        ).join('');
        // 如果当前选中的存档已不存在（被删除），重置为第一个
        const ids = _archiveList.map(a => a.id);
        if (!_currentArchiveId || !ids.includes(_currentArchiveId)) {
            _currentArchiveId = _archiveList[0].id;
            localStorage.setItem('we_archive_id', _currentArchiveId);
        }
        sel.value = _currentArchiveId;
        loadArchiveData();
    }
    sel.onchange = () => { _currentArchiveId = sel.value; localStorage.setItem('we_archive_id', sel.value); loadArchiveData(); };
}

async function loadArchiveData() {
    if (!_currentArchiveId) return;
    try {
        _archiveData = await api('/api/archives/' + _currentArchiveId);
    } catch (e) {
        toast('❌ 加载存档失败，已切回当前数据');
        // 存档不存在或加载失败 → 清除缓存并切回 current 模式
        _currentArchiveId = null;
        localStorage.removeItem('we_archive_id');
        localStorage.setItem('we_archive_mode', 'current');
        switchArchiveMode('current');
        return;
    }
    STATE = _archiveData.state || {};
    _mapLocData = _archiveData.locations || [];
    _mapSelectedLoc = -1;
    renderSessionsFrom(_archiveData.sessions);
    renderEventsFrom(_archiveData.events);
    renderCharsFrom(_archiveData.characters);
    renderMapUI();
    loadWorldSettings();
    clearDirty();
}

function renderSessionsFrom(sessions) {
    const el = $('#tab-sessions');
    const data = sessions || { active: [], archive: [] };
    let html = '<div class="section-title">活跃会话 <span class="section-sub">' + (data.active || []).length + ' 个</span></div>';
    if ((data.active || []).length === 0) {
        html += '<div class="empty">暂无活跃会话</div>';
    } else {
        html += '<div class="grid-2">';
        (data.active || []).forEach(s => {
            const id = s.session_id || s._file;
            const type = s.type || 'unknown';
            const chars = (s.participants || []).join('、');
            const loc = s.location || '';
            const msgs = s.messages || [];
            html += `<div class="card">
        <div class="card-header">${escHtml(id)} <span class="badge">${type}</span></div>
        <div style="padding:10px 16px;font-size:12px;color:#6b7280">📍 ${escHtml(loc)} · ${escHtml(chars)}<br>${msgs.length} 条消息</div>
        </div>`;
        });
        html += '</div>';
    }
    html += '<div class="section-title" style="margin-top:16px">归档会话 <span class="section-sub">' + (data.archive || []).length + ' 个</span></div>';
    if ((data.archive || []).length === 0) html += '<div class="empty">暂无归档</div>';
    el.innerHTML = html;
}

function renderEventsFrom(events) {
    const el = $('#tab-events');
    events = events || [];
    let html = '<div class="section-title">事件日志 <span class="section-sub">' + events.length + ' 条</span></div>';
    if (events.length === 0) {
        html += '<div class="empty">暂无事件记录</div>';
    } else {
        html += '<div class="card"><table class="data-table"><thead><tr><th>时间</th><th>类型</th><th>内容</th></tr></thead><tbody>';
        events.slice().reverse().forEach(e => {
            html += `<tr><td style="white-space:nowrap">#${escHtml(String(e.id || ''))} | ${escHtml(e.time || e.timestamp || '')}</td><td><span class="badge">${escHtml(e.type || '')}</span></td><td>${escHtml(e.description || e.content || JSON.stringify(e))}</td></tr>`;
        });
        html += '</tbody></table></div>';
    }
    el.innerHTML = html;
}

function _parseTextToEntries(text) {
    if (!text || typeof text !== 'string') return Array.isArray(text) ? text : [];
    return text.split('\n')
        .map(line => line.trim())
        .filter(line => line && !line.startsWith('#'))
        .map(line => {
            if (line.startsWith('- ')) line = line.slice(2);
            return { text: line, ttl: '永久', created: '' };
        });
}

function renderCharsFrom(chars) {
    _charData = (chars || []).map(c => ({
        name: c.name,
        public_base: Array.isArray(c.public_base) ? c.public_base : _parseTextToEntries(c.public_base),
        private_base: Array.isArray(c.private_base) ? c.private_base : _parseTextToEntries(c.private_base),
        public_dynamic: Array.isArray(c.public_dynamic) ? c.public_dynamic : _parseTextToEntries(c.public_dynamic),
        private_dynamic: Array.isArray(c.private_dynamic) ? c.private_dynamic : _parseTextToEntries(c.private_dynamic),
    }));
    renderCharsUI();
}

async function archiveSaveEdits() {
    if (!_currentArchiveId) return;
    collectCharData();
    collectCharState();
    const characters = _charData.map(c => ({
        name: c.name,
        public_base: (c.public_base || []).filter(e => (e.text || '').trim()),
        private_base: (c.private_base || []).filter(e => (e.text || '').trim()),
        public_dynamic: (c.public_dynamic || []).filter(e => (e.text || '').trim()),
        private_dynamic: (c.private_dynamic || []).filter(e => (e.text || '').trim()),
    }));
    collectLocData();
    // 收集世界设定
    const wsDate = $('#wsDate');
    const wsTime = $('#wsTime');
    const wsName = $('#wsWorldName');
    const wsPlayer = $('#wsPlayerChar');
    if (wsDate && wsTime) {
        STATE.current_time = wsDate.value + 'T' + wsTime.value + ':00';
    }
    if (wsName) {
        STATE.world_name = wsName.value.trim() || STATE.world_name || '';
    }
    if (wsPlayer) {
        STATE.player_character = wsPlayer.value;
    }
    delete STATE.day_of_week;
    // 收集 lore 数据
    const lore = collectLoreData();
    const body = { state: STATE, characters, locations: _mapLocData, lore };
    await api('/api/archives/' + _currentArchiveId + '/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    });
    toast('存档已保存');
    clearDirty();
}

async function archiveNew() {
    const name = prompt('新存档名称（空白世界）：');
    if (!name) return;
    const r = await api('/api/archives/new', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    if (r.ok) { _currentArchiveId = r.id; localStorage.setItem('we_archive_id', r.id); toast('已创建空白存档'); loadArchiveList(); }
}

async function archiveSaveCurrent() {
    const name = prompt('保存当前数据为存档，输入名称：');
    if (!name) return;
    const r = await api('/api/archives/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
    });
    if (r.ok) { _currentArchiveId = r.id; localStorage.setItem('we_archive_id', r.id); toast('已保存为存档'); loadArchiveList(); }
}

async function archiveRename() {
    if (!_currentArchiveId) return;
    const name = prompt('输入新名称：');
    if (!name) return;
    const r = await api('/api/archives/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: _currentArchiveId, name })
    });
    if (r.id) _currentArchiveId = r.id;
    toast('已重命名');
    loadArchiveList();
}

async function archiveApply() {
    if (!_currentArchiveId) return;
    const p = _archiveList.find(a => a.id === _currentArchiveId);
    if (!confirm('确认将存档「' + (p ? p.name : '') + '」加载到当前？\n当前数据将被覆盖！')) return;
    const r = await api('/api/archives/apply/' + _currentArchiveId, { method: 'POST' });
    if (r.ok) { toast('已加载存档到当前数据'); switchArchiveMode('current'); }
}

async function archiveDelete() {
    if (!_currentArchiveId) return;
    const p = _archiveList.find(a => a.id === _currentArchiveId);
    if (!confirm('确认删除存档「' + (p ? p.name : '') + '」？')) return;
    await api('/api/archives/' + _currentArchiveId, { method: 'DELETE' });
    _currentArchiveId = null;
    localStorage.removeItem('we_archive_id');
    toast('已删除存档');
    loadArchiveList();
}
