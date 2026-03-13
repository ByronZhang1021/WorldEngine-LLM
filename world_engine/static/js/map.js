// ── Map Tab ──────────────────────────────────────────────
let _mapLocData = [];
let _mapSelectedLoc = -1;

// ── Camera state (persisted across re-renders) ──
let _mapVB = null;            // current viewBox {x, y, w, h}
let _mapFitVB = null;         // "fit all" viewBox
let _mapBounds = null;        // content boundary for pan clamping {x1,y1,x2,y2}
let _mapOriginX = 0, _mapOriginY = 0; // world-coord origin for coord conversion

// ── Drag state ──
let _mapDragging = false;
let _mapDragOrigin = null;    // {mx, my, vx, vy}
let _mapDragDist = 0;
let _mapDragTarget = null;

// ── Auto-refresh ──
let _mapRefreshTimer = null;
let _mapDocListeners = false;

// ── Constants ──
const MAP_S = 50;            // SVG pixels per world unit
const MAP_P = 50;            // padding
const MAP_ZOOM_MIN = 0.15;   // max zoom-in  (viewBox shrinks to 15% of fit)
const MAP_ZOOM_MAX = 3.0;    // max zoom-out (viewBox grows to 300% of fit)
const MAP_DRAG_TH = 5;       // px threshold: click vs drag
const MAP_REFRESH = 3000;    // ms between character refreshes

// ──────────────────────────────────────────────────────────
// Data loading
// ──────────────────────────────────────────────────────────
async function loadMap() {
    _mapLocData = await api('/api/locations');
    _mapSelectedLoc = -1;
    renderMapUI();
    _mapStartRefresh();
}

// ──────────────────────────────────────────────────────────
// Full UI Render (left panel + right map)
// ──────────────────────────────────────────────────────────
function renderMapUI() {
    const scrollY = window.scrollY;
    const el = $('#tab-map');
    const charAtLoc = _mapCharAtLoc();

    let html = '<div class="settings-layout" style="padding-bottom:0;align-items:flex-start">';

    // ── Left: Location list ──
    html += '<div class="settings-left">';
    html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">';
    html += '<div class="section-title" style="margin:0">地点列表 <span class="section-sub">' + _mapLocData.length + ' 个</span></div>';
    html += '<button class="btn btn-sm" onclick="mapAddLoc()">+ 新增地点</button>';
    html += '</div>';
    html += '<div id="locListPanel">';
    _mapLocData.forEach((loc, li) => { html += renderLocGroup(loc, li); });
    html += '</div></div>';

    // ── Right: Map (independent height, sticky) ──
    html += '<div class="settings-right" style="display:flex;flex-direction:column;position:sticky;top:60px">';
    html += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">';
    html += '<div class="section-title" style="margin:0">世界地图 <span class="section-sub" style="font-weight:normal">滚轮缩放 · 拖拽平移 · 选中地点后点击设坐标</span></div>';
    html += '<button class="btn btn-sm" onclick="mapResetView()" title="重置视图">⟲ 重置</button>';
    html += '</div>';
    html += '<div class="map-container" id="mapSvgContainer" style="height:500px"></div>';
    html += '</div></div>';
    el.innerHTML = html;

    renderMapSvg(charAtLoc);
    el.querySelectorAll('.loc-entry textarea').forEach(ta => autoResizeTa(ta));
    requestAnimationFrame(() => window.scrollTo(0, scrollY));
}

function _mapCharAtLoc() {
    const stateChars = STATE.characters || {};
    const m = {};
    for (const [name, info] of Object.entries(stateChars)) {
        const loc = info.location || '';
        if (!m[loc]) m[loc] = [];
        m[loc].push(name);
    }
    return m;
}

// ──────────────────────────────────────────────────────────
// SVG Rendering
// ──────────────────────────────────────────────────────────
function renderMapSvg(charAtLoc) {
    const container = $('#mapSvgContainer');
    if (!container) return;

    // World bounds
    let mnX = 0, mxX = 10, mnY = 0, mxY = 6;
    _mapLocData.forEach(l => {
        if (l.x - 1 < mnX) mnX = l.x - 1;
        if (l.x + 1 > mxX) mxX = l.x + 1;
        if (l.y - 1 < mnY) mnY = l.y - 1;
        if (l.y + 1 > mxY) mxY = l.y + 1;
    });
    _mapOriginX = mnX;
    _mapOriginY = mnY;

    const totalW = (mxX - mnX) * MAP_S + MAP_P * 2;
    const totalH = Math.max((mxY - mnY) * MAP_S + MAP_P * 2, 500);

    // Fit viewBox & content bounds
    _mapFitVB = { x: 0, y: 0, w: totalW, h: totalH };
    _mapBounds = { x1: -MAP_S, y1: -MAP_S, x2: totalW + MAP_S, y2: totalH + MAP_S };
    if (!_mapVB) _mapVB = { ..._mapFitVB };

    const vb = _mapVB;
    let svg = `<svg id="mapSvg" viewBox="${vb.x} ${vb.y} ${vb.w} ${vb.h}" preserveAspectRatio="xMidYMid meet">`;

    // Grid
    for (let x = Math.floor(mnX); x <= Math.ceil(mxX); x++) {
        const px = (x - mnX) * MAP_S + MAP_P;
        svg += `<line x1="${px}" y1="${MAP_P - 10}" x2="${px}" y2="${totalH - MAP_P + 10}" stroke="#e5e7eb" stroke-width="0.5"/>`;
        svg += `<text x="${px}" y="${MAP_P - 16}" text-anchor="middle" class="map-ruler-label">${x}</text>`;
    }
    for (let y = Math.floor(mnY); y <= Math.ceil(mxY); y++) {
        const py = (y - mnY) * MAP_S + MAP_P;
        svg += `<line x1="${MAP_P - 10}" y1="${py}" x2="${totalW - MAP_P + 10}" y2="${py}" stroke="#e5e7eb" stroke-width="0.5"/>`;
        svg += `<text x="${MAP_P - 16}" y="${py + 4}" text-anchor="middle" class="map-ruler-label">${y}</text>`;
    }

    // Location markers
    _mapLocData.forEach((l, i) => {
        const cx = (l.x - mnX) * MAP_S + MAP_P;
        const cy = (l.y - mnY) * MAP_S + MAP_P;
        const hasChar = charAtLoc[l.name] && charAtLoc[l.name].length > 0;
        const isSel = i === _mapSelectedLoc;
        const fill = isSel ? '#f97316' : hasChar ? '#7c3aed' : '#d1d5db';
        svg += `<circle cx="${cx}" cy="${cy}" r="8" fill="${fill}" opacity="0.8" style="cursor:pointer" data-loc-i="${i}"/>`;
        svg += `<text x="${cx}" y="${cy - 14}" text-anchor="middle" font-size="11" fill="#374151" font-weight="600">${escHtml(l.name)}</text>`;
        if (hasChar) {
            svg += `<text x="${cx}" y="${cy + 22}" text-anchor="middle" font-size="10" fill="#7c3aed">${charAtLoc[l.name].join('、')}</text>`;
        }
    });

    svg += '</svg>';
    container.innerHTML = svg;
    _mapAttachListeners(container);
}

// ──────────────────────────────────────────────────────────
// Interaction: Zoom, Pan, Click
// ──────────────────────────────────────────────────────────
function _mapAttachListeners(container) {
    container.onwheel = _mapOnWheel;
    container.onmousedown = _mapOnDown;
    if (!_mapDocListeners) {
        document.addEventListener('mousemove', _mapOnMove);
        document.addEventListener('mouseup', _mapOnUp);
        _mapDocListeners = true;
    }
}

function _svgPt(e) {
    const svg = document.querySelector('#mapSvg');
    if (!svg) return null;
    const pt = svg.createSVGPoint();
    pt.x = e.clientX; pt.y = e.clientY;
    const ctm = svg.getScreenCTM();
    return ctm ? pt.matrixTransform(ctm.inverse()) : null;
}

function _mapOnWheel(e) {
    e.preventDefault();
    if (!_mapVB || !_mapFitVB) return;

    // Mouse position in SVG coords BEFORE zoom
    const sp = _svgPt(e);
    if (!sp) return;

    const factor = e.deltaY > 0 ? 1.12 : 0.89;
    let nw = _mapVB.w * factor, nh = _mapVB.h * factor;

    // Clamp zoom
    const minW = _mapFitVB.w * MAP_ZOOM_MIN, maxW = _mapFitVB.w * MAP_ZOOM_MAX;
    const minH = _mapFitVB.h * MAP_ZOOM_MIN, maxH = _mapFitVB.h * MAP_ZOOM_MAX;
    nw = Math.max(minW, Math.min(maxW, nw));
    nh = Math.max(minH, Math.min(maxH, nh));

    // Keep point under mouse fixed
    const svg = document.querySelector('#mapSvg');
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const scale = Math.min(rect.width / _mapVB.w, rect.height / _mapVB.h);
    // fractional position of mouse within rendered content
    const contentW = _mapVB.w * scale, contentH = _mapVB.h * scale;
    const offX = (rect.width - contentW) / 2, offY = (rect.height - contentH) / 2;
    const fx = (e.clientX - rect.left - offX) / contentW;
    const fy = (e.clientY - rect.top - offY) / contentH;

    _mapVB.x = sp.x - fx * nw;
    _mapVB.y = sp.y - fy * nh;
    _mapVB.w = nw;
    _mapVB.h = nh;

    _mapClamp();
    _mapApplyVB();
}

function _mapOnDown(e) {
    if (e.button !== 0) return;
    e.preventDefault();
    _mapDragging = true;
    _mapDragDist = 0;
    _mapDragTarget = e.target;
    _mapDragOrigin = { mx: e.clientX, my: e.clientY, vx: _mapVB.x, vy: _mapVB.y };
    const c = document.querySelector('#mapSvgContainer');
    if (c) c.classList.add('grabbing');
}

function _mapOnMove(e) {
    if (!_mapDragging || !_mapDragOrigin || !_mapVB) return;
    const dx = e.clientX - _mapDragOrigin.mx;
    const dy = e.clientY - _mapDragOrigin.my;
    _mapDragDist = Math.sqrt(dx * dx + dy * dy);

    const svg = document.querySelector('#mapSvg');
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const scale = Math.min(rect.width / _mapVB.w, rect.height / _mapVB.h);

    _mapVB.x = _mapDragOrigin.vx - dx / scale;
    _mapVB.y = _mapDragOrigin.vy - dy / scale;
    _mapClamp();
    _mapApplyVB();
}

function _mapOnUp(e) {
    if (!_mapDragging) return;
    const wasDrag = _mapDragDist > MAP_DRAG_TH;
    _mapDragging = false;
    const c = document.querySelector('#mapSvgContainer');
    if (c) c.classList.remove('grabbing');

    if (!wasDrag) _mapHandleClick(e);
}

function _mapHandleClick(e) {
    // Clicked on a location circle?
    if (_mapDragTarget && _mapDragTarget.tagName === 'circle' && _mapDragTarget.dataset.locI !== undefined) {
        mapSelectLoc(parseInt(_mapDragTarget.dataset.locI));
        return;
    }
    // Set coordinates for selected location
    if (_mapSelectedLoc < 0 || _mapSelectedLoc >= _mapLocData.length) return;
    const sp = _svgPt(e);
    if (!sp) return;
    const worldX = Math.round(((sp.x - MAP_P) / MAP_S + _mapOriginX) * 2) / 2;
    const worldY = Math.round(((sp.y - MAP_P) / MAP_S + _mapOriginY) * 2) / 2;
    collectLocData();
    _mapLocData[_mapSelectedLoc].x = worldX;
    _mapLocData[_mapSelectedLoc].y = worldY;
    toast(_mapLocData[_mapSelectedLoc].name + ' → (' + worldX + ', ' + worldY + ')');
    _mapSelectedLoc = -1;
    markDirty();
    renderMapUI();
}

// ── Boundary clamping ──
function _mapClamp() {
    if (!_mapVB || !_mapBounds) return;
    const b = _mapBounds, v = _mapVB;
    const cw = b.x2 - b.x1, ch = b.y2 - b.y1;
    // Horizontal
    if (v.w >= cw) { v.x = (b.x1 + b.x2) / 2 - v.w / 2; }
    else { v.x = Math.max(b.x1 - v.w * 0.25, Math.min(b.x2 - v.w * 0.75, v.x)); }
    // Vertical
    if (v.h >= ch) { v.y = (b.y1 + b.y2) / 2 - v.h / 2; }
    else { v.y = Math.max(b.y1 - v.h * 0.25, Math.min(b.y2 - v.h * 0.75, v.y)); }
}

function _mapApplyVB() {
    const svg = document.querySelector('#mapSvg');
    if (svg && _mapVB) svg.setAttribute('viewBox', `${_mapVB.x} ${_mapVB.y} ${_mapVB.w} ${_mapVB.h}`);
}

function mapResetView() {
    _mapVB = _mapFitVB ? { ..._mapFitVB } : null;
    _mapApplyVB();
}

// ──────────────────────────────────────────────────────────
// Auto-refresh character positions
// ──────────────────────────────────────────────────────────
function _mapStartRefresh() {
    _mapStopRefresh();
    _mapRefreshTimer = setInterval(_mapRefreshChars, MAP_REFRESH);
}

function _mapStopRefresh() {
    if (_mapRefreshTimer) { clearInterval(_mapRefreshTimer); _mapRefreshTimer = null; }
}

async function _mapRefreshChars() {
    const tab = document.querySelector('#tab-map');
    if (!tab || !tab.classList.contains('active')) return;
    try {
        // 存档模式下不刷新 STATE（STATE 已由 loadArchiveData 设置）
        if (typeof _archiveMode === 'undefined' || _archiveMode === 'current') {
            STATE = await api('/api/state');
        }
        renderMapSvg(_mapCharAtLoc());
    } catch (e) { console.warn('Map refresh:', e); }
}

// ──────────────────────────────────────────────────────────
// Location list helpers (unchanged logic)
// ──────────────────────────────────────────────────────────
function renderLocGroup(loc, li) {
    const entries = loc.entries || [];
    const subLocs = loc.sub_locations || [];
    let h = '<div class="loc-group" data-loc-i="' + li + '">';
    h += '<div class="loc-group-header">';
    h += '<span style="display:flex;align-items:center;gap:6px">';
    h += '<span>📍</span>';
    h += '<input class="loc-title-input" value="' + escHtml(loc.name) + '" data-loc-i="' + li + '" data-field="name" oninput="markDirty()" placeholder="地点名称">';
    h += '<span style="font-weight:400;font-size:11px;color:#9ca3af;white-space:nowrap">(' + loc.x + ', ' + loc.y + ') · ' + entries.length + ' 条</span>';
    h += '</span>';
    h += '<span style="display:flex;gap:4px">';
    h += '<button class="btn btn-sm btn-danger" style="font-size:10px;padding:2px 6px" onclick="event.stopPropagation();mapRemoveLoc(' + li + ')">✕</button>';
    h += '</span></div>';
    // 描述条目
    h += '<div style="padding:4px 10px">';
    entries.forEach((entry, ei) => { h += renderLocEntry(li, ei, entry); });
    h += '<button class="loc-add-entry" onclick="mapAddEntry(' + li + ')">+ 添加条目</button>';
    h += '</div>';
    // 子地点
    h += '<div style="padding:4px 10px;border-top:1px solid #374151">';
    h += '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">';
    h += '<span style="font-size:11px;font-weight:600;color:#9ca3af">🏠 子地点 (' + subLocs.length + ')</span>';
    h += '<button class="loc-add-entry" style="margin:0" onclick="mapAddSubLoc(' + li + ')">+ 添加</button>';
    h += '</div>';
    subLocs.forEach((sl, si) => {
        h += '<div class="sub-loc-entry" data-loc-i="' + li + '" data-sub-i="' + si + '" style="display:flex;align-items:center;gap:6px;margin-bottom:4px">';
        h += '<input class="form-input" style="flex:0 0 80px;font-size:11px;padding:2px 6px" value="' + escHtml(sl.name || '') + '" data-sub-field="name" oninput="markDirty()" placeholder="名称">';
        h += '<input class="form-input" style="flex:1;font-size:11px;padding:2px 6px" value="' + escHtml(sl.description || '') + '" data-sub-field="desc" oninput="markDirty()" placeholder="描述">';
        const defChecked = sl.is_default ? ' checked' : '';
        h += '<label style="font-size:10px;white-space:nowrap;display:flex;align-items:center;gap:2px;cursor:pointer" title="默认子地点">';
        h += '<input type="radio" name="default_sub_' + li + '"' + defChecked + ' onchange="mapSetDefaultSubLoc(' + li + ',' + si + ')">★</label>';
        h += '<button class="loc-entry-del" onclick="mapRemoveSubLoc(' + li + ',' + si + ')" title="删除">×</button>';
        h += '</div>';
    });
    if (subLocs.length === 0) h += '<div style="font-size:10px;color:#6b7280;padding:2px 0">无子地点</div>';
    h += '</div></div>';
    return h;
}

function renderLocEntry(li, ei, entry) {
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
    let h = '<div class="loc-entry" data-loc-i="' + li + '" data-entry-i="' + ei + '" style="display:block">';
    h += '<textarea rows="1" style="width:100%;min-height:26px" oninput="autoResizeTa(this);markDirty()">' + escHtml(entry.text || '') + '</textarea>';
    h += '<div class="loc-meta" style="display:flex;align-items:center">';
    h += '<span style="font-size:10px;color:#9ca3af;flex:1" title="创建时间">' + (created || '-') + '</span>';
    h += '<span style="display:flex;align-items:center;gap:4px">';
    h += '<input type="number" min="0" value="' + ttlNum + '" style="width:50px;' + (ttlUnit === '永久' ? 'display:none' : '') + '" data-ttl-num oninput="markDirty()">';
    h += '<select data-ttl-unit onchange="mapTtlUnitChange(this);markDirty()">';
    ['分钟', '小时', '天', '周', '月', '年', '永久'].forEach(u => {
        h += '<option' + (u === ttlUnit ? ' selected' : '') + '>' + u + '</option>';
    });
    h += '</select>';
    h += '<button class="loc-entry-del" style="margin-left:4px" onclick="mapRemoveEntry(' + li + ',' + ei + ')" title="删除">×</button>';
    h += '</span></div></div>';
    return h;
}

function mapTtlUnitChange(sel) {
    const numInput = sel.parentElement.querySelector('[data-ttl-num]');
    numInput.style.display = sel.value === '永久' ? 'none' : '';
}

function mapSelectLoc(li) {
    collectLocData();
    _mapSelectedLoc = li;
    renderMapSvg(_mapCharAtLoc());
    toast('已选中「' + _mapLocData[li].name + '」，点击地图设置坐标');
}

function mapAddLoc() {
    collectLocData();
    const name = prompt('地点名称：');
    if (!name) return;
    _mapLocData.push({ name, x: 0, y: 0, entries: [], sub_locations: [] });
    markDirty();
    renderMapUI();
}

function mapRemoveLoc(li) {
    if (!confirm('删除地点「' + _mapLocData[li].name + '」？')) return;
    collectLocData();
    _mapLocData.splice(li, 1);
    _mapSelectedLoc = -1;
    markDirty();
    renderMapUI();
}

function mapAddEntry(li) {
    collectLocData();
    const now = (STATE.current_time || new Date().toISOString()).slice(0, 19);
    _mapLocData[li].entries.push({ text: '', ttl: '永久', created: now });
    markDirty();
    renderMapUI();
    setTimeout(() => {
        const grp = $$('.loc-group')[li];
        if (grp) { const tas = grp.querySelectorAll('.loc-entry textarea'); if (tas.length) tas[tas.length - 1].focus(); }
    }, 50);
}

function mapRemoveEntry(li, ei) {
    collectLocData();
    _mapLocData[li].entries.splice(ei, 1);
    markDirty();
    renderMapUI();
}

function collectLocData() {
    $$('.loc-group').forEach(grp => {
        const li = parseInt(grp.dataset.locI);
        if (!_mapLocData[li]) return;
        const nameInput = grp.querySelector('[data-field="name"]');
        if (nameInput) _mapLocData[li].name = nameInput.value;
        grp.querySelectorAll('.loc-entry').forEach(row => {
            const ei = parseInt(row.dataset.entryI);
            if (!_mapLocData[li].entries[ei]) return;
            const ta = row.querySelector('textarea');
            if (ta) _mapLocData[li].entries[ei].text = ta.value;
            const ttlUnit = row.querySelector('[data-ttl-unit]');
            const ttlNum = row.querySelector('[data-ttl-num]');
            if (ttlUnit) {
                const uName = ttlUnit.value;
                const val = Math.round(parseFloat(ttlNum.value) || 0);
                if (uName === '永久' || val <= 0) {
                    _mapLocData[li].entries[ei].ttl = '永久';
                } else if (uName === '分钟') {
                    _mapLocData[li].entries[ei].ttl = val + 'm';
                } else {
                    const hMulti = { '小时': 1, '天': 24, '周': 168, '月': 720, '年': 8760 };
                    const hours = Math.round(val * (hMulti[uName] || 1));
                    _mapLocData[li].entries[ei].ttl = hours > 0 ? String(hours) : '永久';
                }
            }
        });
        grp.querySelectorAll('.sub-loc-entry').forEach(row => {
            const si = parseInt(row.dataset.subI);
            if (!_mapLocData[li].sub_locations) _mapLocData[li].sub_locations = [];
            if (!_mapLocData[li].sub_locations[si]) return;
            const nameEl = row.querySelector('[data-sub-field="name"]');
            const descEl = row.querySelector('[data-sub-field="desc"]');
            if (nameEl) _mapLocData[li].sub_locations[si].name = nameEl.value;
            if (descEl) _mapLocData[li].sub_locations[si].description = descEl.value;
        });
    });
}

function mapAddSubLoc(li) {
    collectLocData();
    if (!_mapLocData[li].sub_locations) _mapLocData[li].sub_locations = [];
    const isFirst = _mapLocData[li].sub_locations.length === 0;
    _mapLocData[li].sub_locations.push({ name: '', description: '', is_default: isFirst });
    markDirty();
    renderMapUI();
}

function mapRemoveSubLoc(li, si) {
    collectLocData();
    const wasDefault = _mapLocData[li].sub_locations[si].is_default;
    _mapLocData[li].sub_locations.splice(si, 1);
    if (wasDefault && _mapLocData[li].sub_locations.length > 0) {
        _mapLocData[li].sub_locations[0].is_default = true;
    }
    markDirty();
    renderMapUI();
}

function mapSetDefaultSubLoc(li, si) {
    collectLocData();
    _mapLocData[li].sub_locations.forEach((sl, i) => { sl.is_default = (i === si); });
    markDirty();
}

async function mapSave() {
    collectLocData();
    await api('/api/locations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(_mapLocData)
    });
    toast('地点已保存');
    clearDirty();
    loadMap();
}
