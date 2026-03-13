// ── World Settings ──
let _loreData = null; // 缓存 lore 数据

async function loadWorldSettings() {
    const el = $('#tab-world-settings');
    const time = STATE.current_time || '';
    const worldName = STATE.world_name || '';
    const playerChar = STATE.player_character || '';
    const dateVal = time.slice(0, 10);
    const timeVal = time.slice(11, 16);
    const dow = getDayOfWeek(time);

    // 获取角色列表：存档模式用已加载的 _charData，当前模式走 API
    let charNames = [];
    if (typeof _archiveMode !== 'undefined' && _archiveMode === 'archive') {
        charNames = _charData.map(c => c.name);
    } else {
        try {
            const chars = await api('/api/characters');
            charNames = chars.map(c => c.name);
        } catch (e) { }
    }

    // 加载 lore 数据
    try {
        if (typeof _archiveMode !== 'undefined' && _archiveMode === 'archive' && _archiveData && _archiveData.lore) {
            _loreData = _archiveData.lore;
        } else {
            _loreData = await api('/api/lore');
        }
    } catch (e) {
        _loreData = { world_premise: '', era: '', tone: '', glossary: {} };
    }

    let html = '<div class="section-title">世界设定</div>';
    html += '<div class="ws-layout">';

    // ── 左栏：基本信息 + 时间 ──
    html += '<div class="ws-left">';
    html += '<div class="card"><div class="card-header">基本信息</div><div class="card-body">';
    html += '<div class="form-group"><label class="form-label">世界名称</label>';
    html += '<input class="form-input" type="text" id="wsWorldName" value="' + escHtml(worldName) + '" oninput="markDirty()"></div>';
    html += '<div class="form-group"><label class="form-label">玩家角色</label>';
    html += '<select class="form-input" id="wsPlayerChar" onchange="markDirty()">';
    for (const name of charNames) {
        const sel = name === playerChar ? ' selected' : '';
        html += `<option value="${escHtml(name)}"${sel}>${escHtml(name)}</option>`;
    }
    html += '</select></div>';
    html += '</div></div>';
    html += '<div class="card" style="margin-top:14px"><div class="card-header">时间设定</div><div class="card-body">';
    html += '<div class="form-group"><label class="form-label">当前日期</label>';
    html += '<input class="form-input" type="date" id="wsDate" value="' + dateVal + '" oninput="markDirty()"></div>';
    html += '<div class="form-group"><label class="form-label">当前时间</label>';
    html += '<input class="form-input" type="time" id="wsTime" value="' + timeVal + '" oninput="markDirty()"></div>';
    html += '<div class="form-group"><label class="form-label">星期（自动计算）</label>';
    html += '<span class="form-input" style="background:#f3f4f6;cursor:default">' + dow + '</span></div>';
    html += '</div></div>';
    html += '</div>'; // ws-left

    // ── 右栏：世界观设定 (Lore) ──
    html += '<div class="ws-right">';
    html += '<div class="card"><div class="card-header">📖 世界观设定</div><div class="card-body">';
    html += '<div class="form-group"><label class="form-label">世界前提 (world_premise)</label>';
    html += '<textarea class="form-textarea" id="lorePremise" rows="3" oninput="markDirty()">' + escHtml(_loreData.world_premise || '') + '</textarea></div>';
    html += '<div class="form-group"><label class="form-label">时代背景 (era)</label>';
    html += '<textarea class="form-textarea" id="loreEra" rows="3" oninput="markDirty()">' + escHtml(_loreData.era || '') + '</textarea></div>';
    html += '<div class="form-group"><label class="form-label">基调 (tone)</label>';
    html += '<textarea class="form-textarea" id="loreTone" rows="3" oninput="markDirty()">' + escHtml(_loreData.tone || '') + '</textarea></div>';
    html += '</div></div>';

    // 术语表 (glossary)
    html += '<div class="card" style="margin-top:14px"><div class="card-header">📚 术语表 (glossary)<button class="btn btn-sm" style="margin-left:auto" onclick="addGlossaryEntry()">＋ 添加词条</button></div>';
    html += '<div class="card-body" id="glossaryContainer">';
    html += renderGlossary(_loreData.glossary || {});
    html += '</div></div>';

    html += '</div>'; // ws-right
    html += '</div>'; // ws-layout
    el.innerHTML = html;

    // 自动调整 textarea 高度
    el.querySelectorAll('textarea').forEach(ta => {
        autoResizeTa(ta);
        ta.addEventListener('input', () => autoResizeTa(ta));
    });
}

function renderGlossary(glossary) {
    const entries = Object.entries(glossary);
    if (entries.length === 0) {
        return '<div class="empty" style="padding:20px">暂无术语词条，点击上方按钮添加</div>';
    }
    let html = '';
    entries.forEach(([key, val], idx) => {
        const pub = (typeof val === 'string') ? val : (val.public || '');
        const sec = (typeof val === 'object') ? (val.secret || '') : '';
        html += `<div class="glossary-entry" data-idx="${idx}">`;
        html += `<div class="glossary-entry-header">`;
        html += `<input class="glossary-name-input" type="text" value="${escHtml(key)}" placeholder="词条名称" oninput="markDirty()">`;
        html += `<button class="btn-xs btn-danger" onclick="removeGlossaryEntry(this)" title="删除词条">✕</button>`;
        html += `</div>`;
        html += `<div class="glossary-entry-body">`;
        html += `<div class="form-group"><label class="form-label">公开描述 (public)</label>`;
        html += `<textarea class="form-textarea glossary-public" rows="2" oninput="markDirty()">${escHtml(pub)}</textarea></div>`;
        html += `<div class="form-group"><label class="form-label">秘密描述 (secret)</label>`;
        html += `<textarea class="form-textarea glossary-secret" rows="2" oninput="markDirty()">${escHtml(sec)}</textarea></div>`;
        html += `</div></div>`;
    });
    return html;
}

function addGlossaryEntry() {
    const container = $('#glossaryContainer');
    // 如果只有 empty 占位，清掉
    const emptyDiv = container.querySelector('.empty');
    if (emptyDiv) emptyDiv.remove();
    const idx = container.querySelectorAll('.glossary-entry').length;
    const div = document.createElement('div');
    div.className = 'glossary-entry';
    div.dataset.idx = idx;
    div.innerHTML = `
        <div class="glossary-entry-header">
            <input class="glossary-name-input" type="text" value="" placeholder="词条名称" oninput="markDirty()">
            <button class="btn-xs btn-danger" onclick="removeGlossaryEntry(this)" title="删除词条">✕</button>
        </div>
        <div class="glossary-entry-body">
            <div class="form-group"><label class="form-label">公开描述 (public)</label>
            <textarea class="form-textarea glossary-public" rows="2" oninput="markDirty()"></textarea></div>
            <div class="form-group"><label class="form-label">秘密描述 (secret)</label>
            <textarea class="form-textarea glossary-secret" rows="2" oninput="markDirty()"></textarea></div>
        </div>`;
    container.appendChild(div);
    div.querySelector('.glossary-name-input').focus();
    markDirty();
}

function removeGlossaryEntry(btn) {
    const entry = btn.closest('.glossary-entry');
    if (entry) {
        entry.remove();
        markDirty();
        // 如果全部删完了，显示空状态
        const container = $('#glossaryContainer');
        if (!container.querySelector('.glossary-entry')) {
            container.innerHTML = '<div class="empty" style="padding:20px">暂无术语词条，点击上方按钮添加</div>';
        }
    }
}

function collectLoreData() {
    const lore = {
        world_premise: $('#lorePremise')?.value || '',
        era: $('#loreEra')?.value || '',
        tone: $('#loreTone')?.value || '',
        glossary: {}
    };
    const entries = document.querySelectorAll('.glossary-entry');
    entries.forEach(entry => {
        const name = entry.querySelector('.glossary-name-input')?.value?.trim();
        if (!name) return;
        const pub = entry.querySelector('.glossary-public')?.value || '';
        const sec = entry.querySelector('.glossary-secret')?.value || '';
        lore.glossary[name] = { public: pub, secret: sec };
    });
    return lore;
}

async function saveWorldSettings() {
    const date = $('#wsDate').value;
    const time = $('#wsTime').value;
    const worldName = $('#wsWorldName').value.trim() || '默认世界';
    const playerChar = $('#wsPlayerChar')?.value || STATE.player_character;
    STATE.current_time = date + 'T' + time + ':00';
    STATE.world_name = worldName;
    STATE.player_character = playerChar;
    delete STATE.day_of_week;

    // 并行保存 state 和 lore
    const lorePayload = collectLoreData();
    await Promise.all([
        api('/api/state', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(STATE)
        }),
        api('/api/lore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(lorePayload)
        })
    ]);

    _loreData = lorePayload;
    const dow = getDayOfWeek(STATE.current_time);
    let timeStr = STATE.current_time.replace('T', ' ').replace(/:\d{2}$/, '');
    $('#headerTime').textContent = `🕐 ${timeStr} ${dow}`;
    toast('世界设定已保存');
    clearDirty();
}

// ── Global Settings ──

// 辅助：获取模型的 pricing 配置
function _getModelPricing(modelName) {
    const pricing = CONFIG.model_pricing || {};
    return pricing[modelName] || {};
}

async function loadSettings() {
    const el = $('#tab-settings');
    const models = CONFIG.models || {};
    const pricing = CONFIG.model_pricing || {};

    const sections = [
        {
            key: 'primary_story', label: '主力剧情模型', pricingType: 'llm', fields: [
                { f: 'model', label: '模型名称', type: 'text' },
                { f: 'temperature', label: 'Temperature', type: 'number', step: 0.05, min: 0, max: 2 },
                { f: 'top_p', label: 'Top P', type: 'number', step: 0.05, min: 0, max: 1 },
            ]
        },
        {
            key: 'secondary_story', label: '辅助剧情模型', pricingType: 'llm', fields: [
                { f: 'model', label: '模型名称', type: 'text' },
                { f: 'temperature', label: 'Temperature', type: 'number', step: 0.05, min: 0, max: 2 },
                { f: 'top_p', label: 'Top P', type: 'number', step: 0.05, min: 0, max: 1 },
            ]
        },
        {
            key: 'analysis', label: '指令分析模型', pricingType: 'llm', fields: [
                { f: 'model', label: '模型名称', type: 'text' },
                { f: 'temperature', label: 'Temperature', type: 'number', step: 0.05, min: 0, max: 2 },
                { f: 'top_p', label: 'Top P', type: 'number', step: 0.05, min: 0, max: 1 },
            ]
        },
        {
            key: 'analysis_reasoning', label: '强推理分析模型', pricingType: 'llm', fields: [
                { f: 'model', label: '模型名称', type: 'text' },
                { f: 'temperature', label: 'Temperature', type: 'number', step: 0.05, min: 0, max: 2 },
                { f: 'top_p', label: 'Top P', type: 'number', step: 0.05, min: 0, max: 1 },
            ]
        },
        {
            key: 'image', label: '图片生成模型', pricingType: 'image', fields: [
                { f: 'model', label: '模型名称', type: 'text' },
                { f: 'num_inference_steps', label: '推理步数', type: 'number', step: 1, min: 1, max: 50 },
            ]
        },
        {
            key: 'embedding', label: '向量嵌入模型', pricingType: 'embedding', fields: [
                { f: 'model', label: '云端模型名称', type: 'text' },
                { f: 'local_model', label: '本地模型名称', type: 'text', placeholder: 'Qwen/Qwen3-Embedding-0.6B' },
            ]
        },
    ];

    let html = '<div class="settings-layout">';
    html += '<div class="settings-left"><div class="settings-models-grid">';

    sections.forEach(sec => {
        const cfg = models[sec.key] || {};
        const modelName = cfg.model || '';
        const mp = pricing[modelName] || {};
        html += `<div class="card"><div class="card-header">${sec.label}</div><div class="card-body">`;
        sec.fields.forEach(fd => {
            const val = cfg[fd.f] !== undefined ? cfg[fd.f] : '';
            const attrs = fd.type === 'number'
                ? `type="number" step="${fd.step || 1}" min="${fd.min || 0}" max="${fd.max || 999}"`
                : `type="text"`;
            html += `<div class="form-group"><label class="form-label">${fd.label}</label>`;
            html += `<input class="form-input" ${attrs} value="${escHtml(String(val))}" data-cfg-key="${sec.key}" data-cfg-field="${fd.f}">`;
            html += `</div>`;
        });
        // 价格配置（内嵌在每个模型卡片底部）
        html += `<div style="border-top:1px solid #e5e7eb;margin-top:8px;padding-top:8px">`;
        const pricingLabel = sec.pricingType === 'image' ? '💰 价格 ($/次)' : '💰 价格 ($/1M tokens)';
        html += `<label class="form-label" style="color:#9ca3af;font-size:11px">${pricingLabel}</label>`;
        if (sec.pricingType === 'image') {
            html += `<div class="form-group"><label class="form-label">每次调用 ($)</label>`;
            html += `<input class="form-input" type="number" step="0.001" min="0" value="${mp.per_call || 0}" data-pricing-key="${sec.key}" data-pricing-field="per_call"></div>`;
        } else {
            html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">`;
            html += `<div class="form-group"><label class="form-label">输入</label>`;
            html += `<input class="form-input" type="number" step="0.01" min="0" value="${mp.input || 0}" data-pricing-key="${sec.key}" data-pricing-field="input"></div>`;
            html += `<div class="form-group"><label class="form-label">输出</label>`;
            html += `<input class="form-input" type="number" step="0.01" min="0" value="${mp.output || 0}" data-pricing-key="${sec.key}" data-pricing-field="output"></div>`;
            html += `</div>`;
        }
        html += `</div>`;
        html += '</div></div>';
    });

    // Embedding 模式选择（插入在 embedding 卡片之后）
    const embMode = (models.embedding || {}).mode || 'cloud';
    const embApiBase = (models.embedding || {}).local_api_base || 'http://localhost:8081';
    html += '<div class="card"><div class="card-header">Embedding 模式</div><div class="card-body">';
    html += '<div class="form-group"><label class="form-label">运行模式</label>';
    html += '<select class="form-input" id="settEmbMode">';
    html += `<option value="cloud"${embMode === 'cloud' ? ' selected' : ''}>☁️ 云端 API</option>`;
    html += `<option value="local"${embMode === 'local' ? ' selected' : ''}>💻 本地模型</option>`;
    html += `<option value="local_api"${embMode === 'local_api' ? ' selected' : ''}>🖥️ 本地 API</option>`;
    html += '</select></div>';
    html += `<div class="form-group"><label class="form-label">本地 API 地址</label><input class="form-input" type="text" value="${escHtml(embApiBase)}" id="settEmbApiBase" placeholder="http://localhost:8081"></div>`;
    html += '<div style="font-size:11px;color:#9ca3af;margin-top:4px">云端：使用 API Base URL 调用（如 302.ai）<br>本地模型：使用 sentence-transformers 本地推理<br>本地 API：调用本地运行的 llama-server 等服务</div>';
    html += '</div></div>';

    // 记忆检索卡片
    const mrCfg = CONFIG.memory_retrieval || {};
    const rrMode = mrCfg.rerank_mode || 'cloud';
    const rrApiBase = mrCfg.local_api_base || 'http://localhost:8082';
    html += '<div class="card"><div class="card-header">记忆检索</div><div class="card-body">';
    // Rerank 模式
    html += '<div class="form-group"><label class="form-label">Rerank 模式</label>';
    html += '<select class="form-input" id="settRerankMode">';
    html += `<option value="cloud"${rrMode === 'cloud' ? ' selected' : ''}>☁️ 云端 API</option>`;
    html += `<option value="local"${rrMode === 'local' ? ' selected' : ''}>💻 本地模型</option>`;
    html += `<option value="local_api"${rrMode === 'local_api' ? ' selected' : ''}>🖥️ 本地 API</option>`;
    html += `<option value="off"${rrMode === 'off' ? ' selected' : ''}>🚫 关闭</option>`;
    html += '</select></div>';
    html += `<div class="form-group"><label class="form-label">本地 API 地址</label><input class="form-input" type="text" value="${escHtml(rrApiBase)}" id="settRrApiBase" placeholder="http://localhost:8082"></div>`;
    html += `<div class="form-group"><label class="form-label">Rerank 云端模型</label><input class="form-input" type="text" value="${escHtml(mrCfg.rerank_model || 'Qwen/Qwen3-Reranker-8B')}" id="settMrRerank"></div>`;
    html += `<div class="form-group"><label class="form-label">Rerank 本地模型</label><input class="form-input" type="text" value="${escHtml(mrCfg.local_rerank_model || 'cross-encoder/ms-marco-MiniLM-L-6-v2')}" id="settMrLocalRerank"></div>`;
    html += `<div class="form-group"><label class="form-label">召回数 (recall_top_k)</label><input class="form-input" type="number" step="1" min="5" max="100" value="${mrCfg.recall_top_k || 30}" id="settMrRecall"></div>`;
    html += `<div class="form-group"><label class="form-label">最终注入数 (final_top_k)</label><input class="form-input" type="number" step="1" min="5" max="50" value="${mrCfg.final_top_k || 15}" id="settMrFinal"></div>`;
    html += `<div class="form-group"><label class="form-label">Embedding 权重</label><input class="form-input" type="number" step="0.05" min="0" max="1" value="${mrCfg.embedding_weight || 0.7}" id="settMrEmbW"></div>`;
    html += `<div class="form-group"><label class="form-label">关键词权重</label><input class="form-input" type="number" step="0.05" min="0" max="1" value="${mrCfg.keyword_weight || 0.3}" id="settMrKwW"></div>`;
    html += `<div class="form-group"><label class="form-label">全量注入阈值</label><input class="form-input" type="number" step="1" min="5" max="100" value="${mrCfg.threshold || 20}" id="settMrThreshold"></div>`;
    html += `<div class="form-group"><label class="form-label">最低分数 (min_score)</label><input class="form-input" type="number" step="0.05" min="0" max="1" value="${mrCfg.min_score || 0.3}" id="settMrMinScore"></div>`;
    // Rerank 模型价格
    const rrModel = mrCfg.rerank_model || 'Qwen/Qwen3-Reranker-8B';
    const rrPricing = pricing[rrModel] || {};
    html += `<div style="border-top:1px solid #e5e7eb;margin-top:8px;padding-top:8px">`;
    html += `<label class="form-label" style="color:#9ca3af;font-size:11px">💰 价格 ($/1M tokens)</label>`;
    html += `<div style="display:grid;grid-template-columns:1fr 1fr;gap:6px">`;
    html += `<div class="form-group"><label class="form-label">输入</label><input class="form-input" type="number" step="0.01" min="0" value="${rrPricing.input || 0}" id="settMrPriceInput"></div>`;
    html += `<div class="form-group"><label class="form-label">输出</label><input class="form-input" type="number" step="0.01" min="0" value="${rrPricing.output || 0}" id="settMrPriceOutput"></div>`;
    html += `</div></div>`;
    html += '</div></div>';

    html += '</div></div>'; // 关闭 settings-models-grid + settings-left

    // ── 右侧区域 ──
    const tgCfg = CONFIG.telegram || {};
    const apiCfg = CONFIG.api || {};
    html += '<div class="settings-right">';

    // API 设置
    html += '<div class="card"><div class="card-header">API 设置</div><div class="card-body">';
    html += `<div class="form-group"><label class="form-label">Base URL</label><input class="form-input" type="text" value="${escHtml(apiCfg.base_url || '')}" id="settApiBaseUrl"></div>`;
    html += `<div class="form-group"><label class="form-label">API Key</label><input class="form-input" type="password" value="${escHtml(apiCfg.api_key || '')}" id="settApiKey"></div>`;
    html += '</div></div>';

    // Telegram
    html += '<div class="card"><div class="card-header">Telegram</div><div class="card-body">';
    html += `<div class="form-group"><label class="form-label">Bot Token (聊天)</label><input class="form-input" type="password" value="${escHtml(tgCfg.bot_token || '')}" id="settTgToken"></div>`;
    html += `<div class="form-group"><label class="form-label">Admin Bot Token (管理)</label><input class="form-input" type="password" value="${escHtml(tgCfg.admin_bot_token || '')}" id="settTgAdminToken"></div>`;
    html += `<div class="form-group"><label class="form-label">Owner ID</label><input class="form-input" type="number" value="${tgCfg.owner_id || ''}" id="settTgOwner"></div>`;
    html += '</div></div>';

    // 世界默认
    const worldCfg = CONFIG.world || {};
    html += '<div class="card"><div class="card-header">世界默认</div><div class="card-body">';
    html += `<div class="form-group"><label class="form-label">默认开始时间</label><input class="form-input" type="text" value="${escHtml(worldCfg.start_time || '2026-01-01T08:00:00')}" id="settWorldStartTime" placeholder="2026-01-01T08:00:00"></div>`;
    html += `<div class="form-group"><label class="form-label">步行速度 (km/h)</label><input class="form-input" type="number" step="1" min="1" max="20" value="${worldCfg.walking_speed_kmh || 6}" id="settWorldWalkSpeed"></div>`;
    html += `<div class="form-group"><label class="form-label">世界模拟阈值 (分钟)</label><input class="form-input" type="number" step="5" min="10" max="480" value="${worldCfg.large_jump_minutes || 60}" id="settWorldLargeJump" title="时间推进超过此分钟数时触发完整世界模拟（活动链生成、重叠检测、离屏场景），低于此值走轻量模拟"></div>`;
    html += `<div class="form-group"><label class="form-label">事件等待窗口 (分钟)</label><input class="form-input" type="number" step="5" min="5" max="120" value="${worldCfg.default_flexible_window || 30}" id="settWorldFlexWindow" title="预定事件超过约定时间后，角色最多等待多少分钟才算过期（具体事件可单独设置）"></div>`;
    html += `<div class="form-group"><label class="form-label">会话压缩阈值 (轮数)</label><input class="form-input" type="number" step="5" min="10" max="100" value="${worldCfg.session_compress_threshold || 30}" id="settWorldCompressThreshold" title="对话超过此轮数后触发自动摘要压缩"></div>`;
    html += '</div></div>';

    // 打字延迟
    const tdCfg = CONFIG.typing_delay || {};
    html += '<div class="card"><div class="card-header">打字延迟</div><div class="card-body">';
    html += `<div class="form-group"><label class="form-label">最少字数 (短于此不分段)</label><input class="form-input" type="number" step="10" min="0" max="500" value="${tdCfg.min_chars || 80}" id="settTdMinChars"></div>`;
    html += `<div class="form-group"><label class="form-label">最多字数 (超过强制分段)</label><input class="form-input" type="number" step="10" min="100" max="2000" value="${tdCfg.max_chars || 500}" id="settTdMaxChars"></div>`;
    html += `<div class="form-group"><label class="form-label">分段偏好</label><select class="form-input" id="settTdBreakPref">`;
    const breakOptions = ['paragraph', 'sentence', 'none'];
    const breakLabels = ['按段落', '按句子', '不分段'];
    breakOptions.forEach((opt, i) => {
        const sel = (tdCfg.break_preference || 'paragraph') === opt ? ' selected' : '';
        html += `<option value="${opt}"${sel}>${breakLabels[i]}</option>`;
    });
    html += `</select></div>`;
    html += '</div></div>';

    html += '</div></div>'; // settings-right + settings-layout

    el.innerHTML = html;
    el.querySelectorAll('input, select').forEach(inp => {
        inp.addEventListener('input', markDirty);
        inp.addEventListener('change', markDirty);
    });
}

async function saveSettings() {
    // 模型配置
    $$('[data-cfg-key]').forEach(input => {
        const key = input.dataset.cfgKey;
        const field = input.dataset.cfgField;
        if (!CONFIG.models[key]) CONFIG.models[key] = {};
        if (input.type === 'number') CONFIG.models[key][field] = parseFloat(input.value);
        else CONFIG.models[key][field] = input.value;
    });
    // Embedding 模式
    if (CONFIG.models.embedding) {
        CONFIG.models.embedding.mode = $('#settEmbMode').value;
        CONFIG.models.embedding.local_api_base = $('#settEmbApiBase').value;
    }

    // 价格配置
    CONFIG.model_pricing = CONFIG.model_pricing || {};
    $$('[data-pricing-key]').forEach(input => {
        const sectionKey = input.dataset.pricingKey;
        const field = input.dataset.pricingField;
        // 通过 section key 找到当前模型名
        const modelName = CONFIG.models[sectionKey]?.model;
        if (modelName) {
            if (!CONFIG.model_pricing[modelName]) CONFIG.model_pricing[modelName] = {};
            CONFIG.model_pricing[modelName][field] = parseFloat(input.value) || 0;
        }
    });
    // Rerank 模型价格
    const rrModel = $('#settMrRerank').value || 'Qwen/Qwen3-Reranker-8B';
    if (!CONFIG.model_pricing[rrModel]) CONFIG.model_pricing[rrModel] = {};
    CONFIG.model_pricing[rrModel].input = parseFloat($('#settMrPriceInput').value) || 0;
    CONFIG.model_pricing[rrModel].output = parseFloat($('#settMrPriceOutput').value) || 0;

    // API
    CONFIG.api.base_url = $('#settApiBaseUrl').value;
    CONFIG.api.api_key = $('#settApiKey').value;

    // Telegram
    CONFIG.telegram = CONFIG.telegram || {};
    CONFIG.telegram.bot_token = $('#settTgToken').value;
    CONFIG.telegram.admin_bot_token = $('#settTgAdminToken').value;
    CONFIG.telegram.owner_id = parseInt($('#settTgOwner').value) || 0;

    // 世界设置
    CONFIG.world = CONFIG.world || {};
    CONFIG.world.start_time = $('#settWorldStartTime').value;
    CONFIG.world.walking_speed_kmh = parseInt($('#settWorldWalkSpeed').value) || 6;
    CONFIG.world.large_jump_minutes = parseInt($('#settWorldLargeJump').value) || 60;
    CONFIG.world.default_flexible_window = parseInt($('#settWorldFlexWindow').value) || 30;
    CONFIG.world.session_compress_threshold = parseInt($('#settWorldCompressThreshold').value) || 30;

    // 记忆检索
    CONFIG.memory_retrieval = CONFIG.memory_retrieval || {};
    CONFIG.memory_retrieval.rerank_mode = $('#settRerankMode').value;
    CONFIG.memory_retrieval.rerank_model = $('#settMrRerank').value;
    CONFIG.memory_retrieval.local_rerank_model = $('#settMrLocalRerank').value;
    CONFIG.memory_retrieval.local_api_base = $('#settRrApiBase').value;
    CONFIG.memory_retrieval.recall_top_k = parseInt($('#settMrRecall').value) || 30;
    CONFIG.memory_retrieval.final_top_k = parseInt($('#settMrFinal').value) || 15;
    CONFIG.memory_retrieval.embedding_weight = parseFloat($('#settMrEmbW').value) || 0.7;
    CONFIG.memory_retrieval.keyword_weight = parseFloat($('#settMrKwW').value) || 0.3;
    CONFIG.memory_retrieval.threshold = parseInt($('#settMrThreshold').value) || 20;
    CONFIG.memory_retrieval.min_score = parseFloat($('#settMrMinScore').value) || 0.3;

    // 打字延迟
    CONFIG.typing_delay = CONFIG.typing_delay || {};
    CONFIG.typing_delay.min_chars = parseInt($('#settTdMinChars').value) || 80;
    CONFIG.typing_delay.max_chars = parseInt($('#settTdMaxChars').value) || 500;
    CONFIG.typing_delay.break_preference = $('#settTdBreakPref').value || 'paragraph';

    await api('/api/config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(CONFIG)
    });
    toast('设置已保存');
    clearDirty();
}
