// ── Characters ──
let _charData = [];
let _selectedChar = 0;
let _deletedChars = [];
let _renamedChars = [];  // [{oldName, newName}, ...]


async function loadCharacters() {
    const chars = await api('/api/characters');
    _charData = chars.map(c => ({
        name: c.name,
        public_base: c.public_base || [],
        private_base: c.private_base || [],
        public_dynamic: c.public_dynamic || [],
        private_dynamic: c.private_dynamic || [],
    }));
    const savedChar = parseInt(localStorage.getItem('we_selected_char')) || 0;
    _selectedChar = savedChar < _charData.length ? savedChar : 0;
    renderCharsUI();
}

function renderCharsUI() {
    const el = $('#tab-characters');
    // 保存所有可滚动容器的位置
    const scrollY = window.scrollY;
    const detailEl = el.querySelector('.char-detail');
    const sidebarEl = el.querySelector('.char-sidebar');
    const detailScroll = detailEl ? detailEl.scrollTop : 0;
    const sidebarScroll = sidebarEl ? sidebarEl.scrollTop : 0;

    const stateChars = STATE.characters || {};
    if (_charData.length === 0) {
        el.innerHTML = '<div class="empty">暂无角色 <button class="btn btn-sm" onclick="charAdd()" style="margin-left:8px">+ 新增角色</button></div>';
        return;
    }
    if (_selectedChar < 0 || _selectedChar >= _charData.length) _selectedChar = 0;

    let html = '<div class="settings-layout" style="padding-bottom:0;align-items:stretch">';

    // ── Left: character list ──
    html += '<div class="char-sidebar">';
    html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">';
    html += '<div class="section-title" style="margin:0;font-size:13px">角色</div>';
    html += '<button class="btn btn-sm" style="font-size:10px" onclick="charAdd()">+ 新增</button>';
    html += '</div>';
    _charData.forEach((c, ci) => {
        const sc = stateChars[c.name] || {};
        const active = ci === _selectedChar ? ' char-item-active' : '';
        html += '<div class="char-item' + active + '" style="display:flex;align-items:center;justify-content:space-between">';
        html += '<div style="flex:1;cursor:pointer" onclick="charSelect(' + ci + ')">';
        html += '<div style="font-weight:600;font-size:13px">' + escHtml(c.name) + '</div>';
        html += '<div style="font-size:11px;color:#9ca3af">📍 ' + escHtml(sc.location || '-') + '</div>';
        html += '</div>';
        html += '<button class="loc-entry-del" onclick="event.stopPropagation();charDelete(' + ci + ')" title="删除" style="flex-shrink:0">×</button>';
        html += '</div>';
    });
    html += '</div>';

    // ── Right: selected character detail ──
    const c = _charData[_selectedChar];
    const ci = _selectedChar;
    const sc = stateChars[c.name] || {};
    html += '<div class="char-detail">';
    html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">';
    html += '<div>';
    html += '<span style="font-size:18px;font-weight:700">' + escHtml(c.name) + '</span>';
    html += '</div>';
    html += '<button class="btn btn-sm" onclick="charRename(' + ci + ')">✏️ 重命名</button>';
    html += '</div>';

    // ── 角色状态 card ──
    const curLoc = sc.location || '';
    const curAct = sc.activity || '';
    const curEmo = sc.emotion || '';
    const curUntil = (sc.until || '').slice(0, 16); // datetime-local needs YYYY-MM-DDTHH:MM
    html += '<div class="card" style="margin-bottom:12px">';
    html += '<div class="card-header">📌 角色状态</div>';
    html += '<div class="card-body" style="display:grid;grid-template-columns:1fr 1fr;gap:8px 12px">';
    // 当前地点
    html += '<div class="form-group" style="margin:0">';
    html += '<label class="form-label" style="font-size:11px">当前地点</label>';
    html += '<select class="form-input" id="charStateLoc" onchange="markDirty();charLocChanged()" style="font-size:12px">';
    const locNames = _mapLocData.map(l => l.name);
    if (!curLoc || !locNames.includes(curLoc)) {
        html += '<option value=""' + (!curLoc ? ' selected' : '') + '>— 未设置 —</option>';
    }
    locNames.forEach(ln => {
        const sel = ln === curLoc ? ' selected' : '';
        html += '<option value="' + escHtml(ln) + '"' + sel + '>' + escHtml(ln) + '</option>';
    });
    html += '</select></div>';
    // 当前子地点
    const curSub = sc.sub_location || '';
    const selLocData = _mapLocData.find(l => l.name === curLoc);
    const subLocs = (selLocData && selLocData.sub_locations) ? selLocData.sub_locations : [];
    html += '<div class="form-group" style="margin:0">';
    html += '<label class="form-label" style="font-size:11px">当前子地点</label>';
    html += '<select class="form-input" id="charStateSubLoc" onchange="markDirty()" style="font-size:12px">';
    if (subLocs.length === 0) {
        html += '<option value="">— 无子地点 —</option>';
    } else {
        if (!curSub || !subLocs.some(s => s.name === curSub)) {
            html += '<option value=""' + (!curSub ? ' selected' : '') + '>— 未设置 —</option>';
        }
        subLocs.forEach(sl => {
            const sel = sl.name === curSub ? ' selected' : '';
            const def = sl.is_default ? ' ★' : '';
            html += '<option value="' + escHtml(sl.name) + '"' + sel + '>' + escHtml(sl.name) + def + '</option>';
        });
    }
    html += '</select></div>';
    // 活动截止
    html += '<div class="form-group" style="margin:0">';
    html += '<label class="form-label" style="font-size:11px">活动截止</label>';
    html += '<input class="form-input" type="datetime-local" id="charStateUntil" value="' + curUntil + '" oninput="markDirty()" style="font-size:12px"></div>';
    // 当前活动 (span full width)
    html += '<div class="form-group" style="margin:0;grid-column:1/-1">';
    html += '<label class="form-label" style="font-size:11px">当前活动</label>';
    html += '<textarea class="form-input" id="charStateAct" rows="1" oninput="markDirty()" style="font-size:12px;resize:none">' + escHtml(curAct) + '</textarea></div>';
    // 情绪状态 (span full width)
    html += '<div class="form-group" style="margin:0;grid-column:1/-1">';
    html += '<label class="form-label" style="font-size:11px">情绪状态</label>';
    html += '<textarea class="form-input" id="charStateEmo" rows="1" oninput="markDirty()" style="font-size:12px;resize:none">' + escHtml(curEmo) + '</textarea></div>';
    html += '</div></div>';

    // ── 已知地点 card（树形：父地点→子地点） ──
    const knownLocs = (sc.known_locations && Array.isArray(sc.known_locations)) ? sc.known_locations : [];
    const knownSubLocs = (sc.known_sub_locations && typeof sc.known_sub_locations === 'object') ? sc.known_sub_locations : {};
    const allLocNames = _mapLocData.map(l => l.name);
    const allChecked = allLocNames.length > 0 && allLocNames.every(ln => knownLocs.includes(ln));
    html += '<div class="card" style="margin-bottom:12px">';
    html += '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center">';
    html += '<span>📍 已知地点</span>';
    html += '<div style="display:flex;align-items:center;gap:8px">';
    html += '<span style="font-size:10px;color:#9ca3af;font-weight:400">角色只能前往已知地点，默认子地点始终可见</span>';
    html += '<button class="loc-add-entry" style="margin:0" onclick="charToggleAllLocs()">' + (allChecked ? '取消全选' : '全选') + '</button>';
    html += '</div></div>';
    html += '<div style="padding:8px 12px">';
    if (allLocNames.length === 0) {
        html += '<div class="empty" style="padding:4px 0">暂无地点（先在地图页添加）</div>';
    } else {
        allLocNames.forEach(ln => {
            const checked = knownLocs.includes(ln) ? ' checked' : '';
            const locData = _mapLocData.find(l => l.name === ln);
            const subs = (locData && locData.sub_locations) ? locData.sub_locations : [];
            html += '<div style="margin-bottom:4px">';
            html += '<label style="display:flex;align-items:center;gap:4px;font-size:12px;cursor:pointer">';
            html += '<input type="checkbox" class="known-loc-cb" value="' + escHtml(ln) + '"' + checked + ' onchange="markDirty()">';
            html += '<strong>' + escHtml(ln) + '</strong>';
            if (subs.length > 0) html += ' <span style="font-size:10px;color:#6b7280">(' + subs.length + ' 子地点)</span>';
            html += '</label>';
            // 子地点勾选
            if (subs.length > 0) {
                const charKnownSubs = knownSubLocs[ln] || [];
                const defaultSub = subs.find(s => s.is_default);
                const defaultSubName = defaultSub ? defaultSub.name : (subs[0] ? subs[0].name : '');
                html += '<div style="margin-left:22px;display:flex;flex-wrap:wrap;gap:2px 10px">';
                subs.forEach(sl => {
                    const isDefault = sl.name === defaultSubName;
                    const subChecked = isDefault || charKnownSubs.includes(sl.name) ? ' checked' : '';
                    const disabled = isDefault ? ' disabled' : '';
                    html += '<label style="display:flex;align-items:center;gap:3px;font-size:11px;cursor:pointer;color:' + (isDefault ? '#f59e0b' : '#d1d5db') + '">';
                    html += '<input type="checkbox" class="known-sub-cb" data-parent="' + escHtml(ln) + '" value="' + escHtml(sl.name) + '"' + subChecked + disabled + ' onchange="markDirty()">';
                    html += escHtml(sl.name) + (isDefault ? ' ★' : '') + '</label>';
                });
                html += '</div>';
            }
            html += '</div>';
        });
    }
    html += '</div></div>';

    // 四个 section（全部条目式，格式完全相同）
    html += renderCharEntrySection(ci, 'public_base', '🔓 公开设定', c.public_base, '外貌、身份、说话风格……他人可见，系统不改');
    html += renderCharEntrySection(ci, 'private_base', '🔒 私密设定', c.private_base, '性格、内在、隐藏动机……只有自己知道，系统不改');
    html += renderCharEntrySection(ci, 'public_dynamic', '🔄 公开动态', c.public_dynamic, '当前外观变化、受伤……他人可见，系统可改');
    html += renderCharEntrySection(ci, 'private_dynamic', '🔄 私密动态', c.private_dynamic, '记忆、心理变化、对人的印象……只有自己知道，系统可改');

    html += '</div></div>';
    el.innerHTML = html;
    el.querySelectorAll('.char-entry textarea').forEach(ta => autoResizeTa(ta));
    requestAnimationFrame(() => {
        window.scrollTo(0, scrollY);
        const newDetail = el.querySelector('.char-detail');
        const newSidebar = el.querySelector('.char-sidebar');
        if (newDetail) newDetail.scrollTop = detailScroll;
        if (newSidebar) newSidebar.scrollTop = sidebarScroll;
    });
}

function renderCharEntrySection(ci, field, label, entries, hint) {
    let h = '<div class="card" style="margin-bottom:12px">';
    h += '<div class="card-header" style="display:flex;justify-content:space-between;align-items:center">';
    h += '<span>' + label + '</span>';
    h += '<div style="display:flex;align-items:center;gap:8px"><span style="font-size:10px;color:#9ca3af;font-weight:400">' + (hint || '') + '</span>';
    h += '<button class="loc-add-entry" style="margin:0" onclick="charAddLine(' + ci + ',\'' + field + '\')">+ 添加条目</button></div>';
    h += '</div>';
    h += '<div style="padding:4px 10px">';
    entries.forEach((entry, li) => {
        const ttl = entry.ttl || '永久';
        const created = (entry.created || '').replace('T', ' ').slice(0, 16);
        let ttlNum = '', ttlUnit = '永久';
        if (ttl === '永久') { ttlUnit = '永久'; }
        else {
            let hrs;
            if (ttl.endsWith('m')) { hrs = parseInt(ttl) / 60; }
            else { hrs = parseFloat(ttl); }
            if (isNaN(hrs) || hrs <= 0) { ttlUnit = '永久'; }
            else if (hrs >= 8760 && hrs % 8760 === 0) { ttlNum = hrs / 8760; ttlUnit = '年'; }
            else if (hrs >= 720 && hrs % 720 === 0) { ttlNum = hrs / 720; ttlUnit = '月'; }
            else if (hrs >= 168 && hrs % 168 === 0) { ttlNum = hrs / 168; ttlUnit = '周'; }
            else if (hrs >= 24 && hrs % 24 === 0) { ttlNum = hrs / 24; ttlUnit = '天'; }
            else if (hrs >= 1) { ttlNum = Math.round(hrs); ttlUnit = '小时'; }
            else { ttlNum = Math.round(hrs * 60); ttlUnit = '分钟'; }
        }
        h += '<div class="char-entry loc-entry" data-char-i="' + ci + '" data-field="' + field + '" data-line-i="' + li + '" style="display:block">';
        h += '<textarea rows="1" style="width:100%;min-height:26px" oninput="autoResizeTa(this);markDirty()">' + escHtml(entry.text || '') + '</textarea>';
        h += '<div class="loc-meta" style="display:flex;align-items:center;margin-top:2px">';
        h += '<span style="font-size:10px;color:#9ca3af;flex:1" title="创建时间">' + (created || '-') + '</span>';
        h += '<span style="display:flex;align-items:center;gap:4px">';
        h += '<input type="number" min="0" value="' + ttlNum + '" style="width:50px;' + (ttlUnit === '永久' ? 'display:none' : '') + '" data-ttl-num oninput="markDirty()">';
        h += '<select data-ttl-unit onchange="charTtlUnitChange(this);markDirty()">';
        ['分钟', '小时', '天', '周', '月', '年', '永久'].forEach(u => {
            h += '<option' + (u === ttlUnit ? ' selected' : '') + '>' + u + '</option>';
        });
        h += '</select>';
        h += '<button class="loc-entry-del" style="margin-left:4px" onclick="charRemoveLine(' + ci + ',\'' + field + '\',' + li + ')" title="删除">×</button>';
        h += '</span></div>';
        h += '</div>';
    });
    if (entries.length === 0) h += '<div class="empty" style="padding:8px 0">暂无内容</div>';
    h += '</div></div>';
    return h;
}

function charTtlUnitChange(sel) {
    const numInput = sel.parentElement.querySelector('[data-ttl-num]');
    numInput.style.display = sel.value === '永久' ? 'none' : '';
}

function charSelect(ci) {
    collectCharData();
    collectCharState();
    _selectedChar = ci;
    localStorage.setItem('we_selected_char', ci);
    renderCharsUI();
}

function collectCharData() {
    $$('.char-entry').forEach(row => {
        const ci = parseInt(row.dataset.charI);
        const field = row.dataset.field;
        const li = parseInt(row.dataset.lineI);
        if (!_charData[ci]) return;
        if (!_charData[ci][field] || !_charData[ci][field][li]) return;
        const ta = row.querySelector('textarea');
        if (ta) _charData[ci][field][li].text = ta.value;
        const ttlUnit = row.querySelector('[data-ttl-unit]');
        const ttlNum = row.querySelector('[data-ttl-num]');
        if (ttlUnit && ttlNum) {
            const uName = ttlUnit.value;
            const val = Math.round(parseFloat(ttlNum.value) || 0);
            if (uName === '永久' || val <= 0) {
                _charData[ci][field][li].ttl = '永久';
            } else if (uName === '分钟') {
                _charData[ci][field][li].ttl = val + 'm';
            } else {
                const hMulti = { '小时': 1, '天': 24, '周': 168, '月': 720, '年': 8760 };
                const hours = Math.round(val * (hMulti[uName] || 1));
                _charData[ci][field][li].ttl = hours > 0 ? String(hours) : '永久';
            }
        }
    });
}

function charAddLine(ci, field) {
    collectCharData();
    const now = (STATE.current_time || new Date().toISOString()).slice(0, 19);
    _charData[ci][field].push({ text: '', ttl: '永久', created: now });
    markDirty();
    renderCharsUI();
}

function charRemoveLine(ci, field, li) {
    collectCharData();
    _charData[ci][field].splice(li, 1);
    markDirty();
    renderCharsUI();
}

function charAdd() {
    const name = prompt('角色名称：');
    if (!name) return;
    collectCharData();
    const now = (STATE.current_time || new Date().toISOString()).slice(0, 19);
    _charData.push({
        name,
        public_base: [],
        private_base: [],
        public_dynamic: [],
        private_dynamic: [],
    });
    _selectedChar = _charData.length - 1;
    markDirty();
    renderCharsUI();
}

function charDelete(ci) {
    if (!confirm('删除角色「' + _charData[ci].name + '」？')) return;
    collectCharData();
    _deletedChars.push(_charData[ci].name);
    _charData.splice(ci, 1);
    if (_selectedChar >= _charData.length) _selectedChar = _charData.length - 1;
    markDirty();
    renderCharsUI();
}

function charRename(ci) {
    const oldName = _charData[ci].name;
    const name = prompt('新名称：', oldName);
    if (!name || name === oldName) return;
    collectCharData();
    collectCharState();
    _charData[ci].name = name;
    // 记录重命名，保存时删除旧文件
    _renamedChars.push({ oldName, newName: name });
    // 迁移 STATE.characters 中的状态数据
    if (STATE.characters && STATE.characters[oldName]) {
        STATE.characters[name] = STATE.characters[oldName];
        delete STATE.characters[oldName];
    }
    markDirty();
    renderCharsUI();
}

function collectCharState() {
    // 收集当前选中角色的状态字段
    if (_charData.length === 0) return;
    const c = _charData[_selectedChar];
    if (!c) return;
    const name = c.name;
    if (!STATE.characters) STATE.characters = {};
    if (!STATE.characters[name]) STATE.characters[name] = {};
    const sc = STATE.characters[name];
    const locEl = document.getElementById('charStateLoc');
    const actEl = document.getElementById('charStateAct');
    const emoEl = document.getElementById('charStateEmo');
    const untilEl = document.getElementById('charStateUntil');
    if (locEl) sc.location = locEl.value;
    const subLocEl = document.getElementById('charStateSubLoc');
    if (subLocEl) sc.sub_location = subLocEl.value;
    if (actEl) sc.activity = actEl.value;
    if (emoEl) sc.emotion = emoEl.value;
    if (untilEl) sc.until = untilEl.value ? untilEl.value + ':00' : '';
    // 收集已知地点
    const cbs = document.querySelectorAll('.known-loc-cb');
    if (cbs.length > 0) {
        sc.known_locations = [];
        cbs.forEach(cb => { if (cb.checked) sc.known_locations.push(cb.value); });
    }
    // 收集已知子地点
    const subCbs = document.querySelectorAll('.known-sub-cb');
    if (subCbs.length > 0) {
        const ksl = {};
        subCbs.forEach(cb => {
            if (cb.checked && !cb.disabled) {
                const parent = cb.dataset.parent;
                if (!ksl[parent]) ksl[parent] = [];
                ksl[parent].push(cb.value);
            }
        });
        sc.known_sub_locations = ksl;
    }
}

function charToggleAllLocs() {
    collectCharData();
    collectCharState();
    const c = _charData[_selectedChar];
    if (!c) return;
    const name = c.name;
    if (!STATE.characters) STATE.characters = {};
    if (!STATE.characters[name]) STATE.characters[name] = {};
    const sc = STATE.characters[name];
    const allLocNames = _mapLocData.map(l => l.name);
    const allChecked = allLocNames.length > 0 && allLocNames.every(ln => (sc.known_locations || []).includes(ln));
    sc.known_locations = allChecked ? [] : [...allLocNames];
    markDirty();
    renderCharsUI();
}

function charLocChanged() {
    // 当地点选择器变化时，更新子地点下拉框
    const locEl = document.getElementById('charStateLoc');
    const subLocEl = document.getElementById('charStateSubLoc');
    if (!locEl || !subLocEl) return;
    const newLoc = locEl.value;
    const locData = _mapLocData.find(l => l.name === newLoc);
    const subs = (locData && locData.sub_locations) ? locData.sub_locations : [];
    let opts = '';
    if (subs.length === 0) {
        opts = '<option value="">— 无子地点 —</option>';
    } else {
        subs.forEach(sl => {
            const def = sl.is_default ? ' ★' : '';
            const sel = sl.is_default ? ' selected' : '';
            opts += '<option value="' + escHtml(sl.name) + '"' + sel + '>' + escHtml(sl.name) + def + '</option>';
        });
    }
    subLocEl.innerHTML = opts;
}
async function saveAllChars() {
    collectCharData();
    collectCharState();

    // 第一步：先完成删除和重命名（必须在保存新数据之前完成，避免竞态）
    const preTasks = [];
    _deletedChars.forEach(name => {
        preTasks.push(api(`/api/character/${encodeURIComponent(name)}`, { method: 'DELETE' }));
        if (STATE.characters) delete STATE.characters[name];
    });
    _deletedChars = [];
    _renamedChars.forEach(({ oldName }) => {
        preTasks.push(api(`/api/character/${encodeURIComponent(oldName)}`, { method: 'DELETE' }));
    });
    _renamedChars = [];
    if (preTasks.length > 0) await Promise.all(preTasks);

    // 第二步：保存 STATE（含已清理的角色状态），必须在角色数据保存之前完成
    // 这样后端 api_save_character 检查 state 时能找到新名字，不会重复注册
    await api('/api/state', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(STATE)
    });

    // 第三步：批量保存所有角色数据（单次请求）
    await api('/api/characters/bulk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ characters: _charData })
    });
    toast('所有角色已保存');
    clearDirty();
}
