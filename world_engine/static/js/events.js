// ── Events ──

// ── 预定事件 CRUD ──

let _scheduledEvents = [];
let _editingEventId = null;

async function loadEvents() {
    // 并行加载离屏事件和预定事件
    const [events, scheduled] = await Promise.all([
        api('/api/events'),
        api('/api/scheduled-events'),
    ]);
    _scheduledEvents = scheduled || [];

    const el = $('#tab-events');

    // 记住当前展开的折叠
    const openIds = new Set();
    el.querySelectorAll('.collapse-body.open').forEach(body => {
        const card = body.closest('.card');
        if (card) openIds.add(card.dataset.eid);
    });

    let html = '';

    // ══════════ 预定事件 ══════════
    html += '<div class="section-title">📅 预定事件 <span class="section-sub">' + _scheduledEvents.length + ' 条</span>';
    html += ' <button class="btn-sm" onclick="showAddEventForm()" style="margin-left:12px">➕ 新增</button>';
    html += '</div>';

    // 新增/编辑表单
    html += '<div id="eventForm" style="display:none" class="card" style="margin-bottom:16px">';
    html += `<div class="card-body">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
            <div><label style="font-size:12px;color:#6b7280">时间 (ISO)</label>
                <input id="evtTime" class="input-field" placeholder="2026-01-08T10:00:00"></div>
            <div><label style="font-size:12px;color:#6b7280">地点</label>
                <input id="evtLocation" class="input-field" placeholder="咖啡厅"></div>
        </div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
            <div><label style="font-size:12px;color:#6b7280">子地点</label>
                <input id="evtSubLocation" class="input-field" placeholder="二楼包间"></div>
            <div><label style="font-size:12px;color:#6b7280">参与角色 (逗号分隔)</label>
                <input id="evtParticipants" class="input-field" placeholder="角色A,角色B"></div>
        </div>
        <div style="margin-bottom:8px"><label style="font-size:12px;color:#6b7280">描述</label>
            <input id="evtDescription" class="input-field" placeholder="一起喝下午茶"></div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:8px">
            <div><label style="font-size:12px;color:#6b7280">愿意等待 (分钟)</label>
                <input id="evtWindow" class="input-field" type="number" value="30"></div>
            <div><label style="font-size:12px;color:#6b7280">创建者</label>
                <input id="evtCreatedBy" class="input-field" placeholder="dashboard"></div>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end">
            <button class="btn-sm" onclick="hideEventForm()">取消</button>
            <button class="btn-sm" style="background:#059669;color:#fff" onclick="submitEvent()">保存</button>
        </div>
    </div>`;
    html += '</div>';

    if (_scheduledEvents.length === 0) {
        html += '<div class="empty">暂无预定事件</div>';
    } else {
        html += '<div class="grid-2">';
        // 按状态分组：pending 在前
        const pending = _scheduledEvents.filter(e => (e.status || 'pending') === 'pending');
        const done = _scheduledEvents.filter(e => (e.status || 'pending') !== 'pending');
        const sorted = [...pending.sort((a, b) => (a.time || '').localeCompare(b.time || '')), ...done];

        sorted.forEach(e => {
            const eid = e.id || '';
            const status = e.status || 'pending';
            const statusBadge = {
                'pending': '<span class="badge" style="background:#fef3c7;color:#92400e">待执行</span>',
                'completed': '<span class="badge" style="background:#d1fae5;color:#065f46">已完成</span>',
                'missed': '<span class="badge" style="background:#fee2e2;color:#991b1b">已错过</span>',
            }[status] || `<span class="badge">${escHtml(status)}</span>`;
            const time = (e.time || '').replace('T', ' ').slice(0, 16);
            const loc = e.location || '';
            const subLoc = e.sub_location || '';
            const locDisplay = subLoc ? `${loc}/${subLoc}` : loc;
            const chars = (e.participants || []).join('、');
            const desc = e.description || '';
            const window = e.flexible_window || 60;
            const createdBy = e.created_by || '';
            const createdAt = (e.created_at || '').replace('T', ' ').slice(0, 16);

            html += `<div class="card" data-eid="${escHtml(eid)}">
                <div class="card-header" style="display:flex;justify-content:space-between;align-items:center">
                    <span>📅 ${escHtml(time)} ${statusBadge}</span>
                    ${status === 'pending' ? `<span style="display:flex;gap:4px">
                        <button class="btn-xs" onclick="editScheduledEvent('${escHtml(eid)}')">✏️</button>
                        <button class="btn-xs btn-danger" onclick="deleteScheduledEvent('${escHtml(eid)}')">🗑️</button>
                    </span>` : ''}
                </div>
                <div class="card-body" style="font-size:12px;color:#374151">
                    <div style="margin-bottom:4px"><strong>${escHtml(desc)}</strong></div>
                    ${locDisplay ? `<div>📍 ${escHtml(locDisplay)}</div>` : ''}
                    ${chars ? `<div>👥 ${escHtml(chars)}</div>` : ''}
                    <div style="color:#9ca3af;font-size:11px">等${window}分钟 · 创建: ${escHtml(createdBy)}${createdAt ? ` · 🕐 ${escHtml(createdAt)}` : ''}</div>
                </div>
            </div>`;
        });
        html += '</div>';
    }

    // ══════════ 离屏事件日志 ══════════
    html += '<div class="section-title" style="margin-top:24px">📝 事件日志 <span class="section-sub">' + events.length + ' 条</span></div>';
    if (events.length === 0) {
        html += '<div class="empty">暂无事件记录</div>';
    } else {
        html += '<div class="grid-2">';
        events.slice().reverse().forEach(e => {
            const eid = String(e.id || e.time || '');
            const time = e.time || e.timestamp || '';
            const endTime = e.end_time || '';
            const loc = e.location || '';
            const chars = (e.characters || []).join('、');
            const _typeMap = {
                'phone_call': '📞 电话',
                'interact': '🤝 互动',
                'seek': '🔍 找人',
                'npc_initiated': '🤖 NPC发起',
            };
            const type = _typeMap[e.type] || e.type || '离屏事件';
            const summary = e.summary || e.description || e.content || '';
            const scene = e.scene || '';
            const isOpen = openIds.has(eid);
            const timeDisplay = time.replace('T', ' ').replace(/:\d{2}$/, '');
            const endTimeDisplay = endTime ? endTime.replace('T', ' ').replace(/:\d{2}$/, '') : '';

            html += `<div class="card" data-eid="${escHtml(eid)}">
                <div class="card-header">#${escHtml(eid)} | ${escHtml(timeDisplay)}${endTimeDisplay ? ' → ' + escHtml(endTimeDisplay) : ''} <span class="badge">${escHtml(type)}</span></div>
                <div class="card-body" style="font-size:12px;color:#6b7280">
                    📍 ${escHtml(loc)} · ${escHtml(chars)}<br>
                    <span style="font-size:12px;color:#374151;margin-top:4px;display:inline-block">${escHtml(summary)}</span>
                </div>
                ${scene ? `<div class="collapse-header" onclick="toggleCollapse(this)">
                    <span class="icon">${isOpen ? '▼' : '▶'}</span> 查看完整场景
                </div>
                <div class="collapse-body${isOpen ? ' open' : ''}">
                    <div style="font-size:12px;color:#374151;line-height:1.8;white-space:pre-wrap">${escHtml(scene)}</div>
                </div>` : ''}
            </div>`;
        });
        html += '</div>';
    }
    el.innerHTML = html;
}

// ── 预定事件表单 ──

function showAddEventForm() {
    _editingEventId = null;
    const form = document.getElementById('eventForm');
    if (form) {
        form.style.display = 'block';
        // 清空表单
        _setVal('evtTime', '');
        _setVal('evtLocation', '');
        _setVal('evtSubLocation', '');
        _setVal('evtParticipants', '');
        _setVal('evtDescription', '');
        _setVal('evtWindow', '30');
        _setVal('evtCreatedBy', 'dashboard');
    }
}

function editScheduledEvent(eventId) {
    const evt = _scheduledEvents.find(e => e.id === eventId);
    if (!evt) return;
    _editingEventId = eventId;
    const form = document.getElementById('eventForm');
    if (form) {
        form.style.display = 'block';
        _setVal('evtTime', evt.time || '');
        _setVal('evtLocation', evt.location || '');
        _setVal('evtSubLocation', evt.sub_location || '');
        _setVal('evtParticipants', (evt.participants || []).join(','));
        _setVal('evtDescription', evt.description || '');
        _setVal('evtWindow', String(evt.flexible_window || 60));
        _setVal('evtCreatedBy', evt.created_by || '');
    }
}

function hideEventForm() {
    const form = document.getElementById('eventForm');
    if (form) form.style.display = 'none';
    _editingEventId = null;
}

async function submitEvent() {
    const time = _getVal('evtTime');
    if (!time) { toast('请填写时间'); return; }
    const data = {
        time,
        location: _getVal('evtLocation'),
        sub_location: _getVal('evtSubLocation'),
        participants: _getVal('evtParticipants').split(',').map(s => s.trim()).filter(Boolean),
        description: _getVal('evtDescription'),
        flexible_window: parseInt(_getVal('evtWindow')) || 30,
        created_by: _getVal('evtCreatedBy') || 'dashboard',
    };

    try {
        if (_editingEventId) {
            await api(`/api/scheduled-events/${_editingEventId}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            toast('预定事件已更新');
        } else {
            await api('/api/scheduled-events', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data),
            });
            toast('预定事件已添加');
        }
        hideEventForm();
        loadEvents();
    } catch (e) {
        toast('操作失败: ' + e.message);
    }
}

async function deleteScheduledEvent(eventId) {
    if (!confirm('确定删除该预定事件？')) return;
    try {
        await api(`/api/scheduled-events/${eventId}`, { method: 'DELETE' });
        toast('预定事件已删除');
        loadEvents();
    } catch (e) {
        toast('删除失败: ' + e.message);
    }
}

// helpers
function _getVal(id) { const el = document.getElementById(id); return el ? el.value : ''; }
function _setVal(id, v) { const el = document.getElementById(id); if (el) el.value = v; }
