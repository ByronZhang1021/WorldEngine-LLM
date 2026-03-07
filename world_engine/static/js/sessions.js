// ── Sessions ──
let _sessionsTimer = null;

async function loadSessions() {
  const data = await api('/api/sessions');
  const el = $('#tab-sessions');

  // 更新 header 时间
  let timeStr = (STATE.current_time || '').replace('T', ' ').replace(/:\d{2}$/, '');
  let dow = getDayOfWeek(STATE.current_time);
  $('#headerTime').textContent = `🕐 ${timeStr} ${dow}`;

  // 记住当前展开的折叠
  const openIds = new Set();
  el.querySelectorAll('.collapse-body.open').forEach(body => {
    const card = body.closest('.card');
    if (card) openIds.add(card.dataset.sid);
  });

  let html = '<div class="section-title">活跃会话 <span class="section-sub">' + data.active.length + ' 个</span></div>';
  if (data.active.length === 0) {
    html += '<div class="empty">暂无活跃会话</div>';
  } else {
    html += '<div class="grid-2">';
    data.active.forEach((s, i) => {
      const id = s.session_id || s._file;
      const type = s.type || 'unknown';
      const chars = (s.participants || []).join('、');
      const loc = s.location || '';
      const msgs = (s.messages || []);
      const isOpen = openIds.has(String(id));
      html += `<div class="card" data-sid="${escHtml(String(id))}">
        <div class="card-header">${escHtml(String(id))} <span class="badge">${type}</span></div>
        <div class="card-body" style="font-size:12px;color:#6b7280">
          📍 ${escHtml(loc)} · ${escHtml(chars)}<br>
          <span style="font-size:11px">${msgs.length} 条消息</span>
        </div>
        <div class="collapse-header" onclick="toggleCollapse(this)">
          <span class="icon">${isOpen ? '▼' : '▶'}</span> 查看对话
        </div>
        <div class="collapse-body${isOpen ? ' open' : ''}">
          ${msgs.map(m => {
        const speaker = m.speaker || m.role || '?';
        const text = m.text || m.content || '';
        const time = m.time || '';
        const isUser = speaker === (STATE.player_character || '');
        const isSystem = speaker === 'system';
        const roleClass = isUser ? 'user' : isSystem ? 'system' : '';
        return `<div class="msg"><span class="role ${roleClass}">[${escHtml(time)}] ${escHtml(speaker)}</span><span class="content">${escHtml(text.substring(0, 500))}</span></div>`;
      }).join('')}
          ${msgs.length === 0 ? '<div class="empty">暂无消息</div>' : ''}
        </div>
      </div>`;
    });
    html += '</div>';
  }
  html += '<div class="section-title" style="margin-top:24px">归档会话 <span class="section-sub">' + data.archive.length + ' 个</span></div>';
  if (data.archive.length === 0) {
    html += '<div class="empty">暂无归档</div>';
  } else {
    html += '<div class="grid-2">';
    data.archive.forEach(s => {
      const id = s.session_id || s._file;
      const type = s.type || 'unknown';
      const chars = (s.participants || []).join('、');
      const loc = s.location || '';
      const msgs = (s.messages || []);
      const isOpen = openIds.has(String(id));
      html += `<div class="card" data-sid="${escHtml(String(id))}">
        <div class="card-header">${escHtml(String(id))} <span class="badge">${type}</span></div>
        <div class="card-body" style="font-size:12px;color:#6b7280">
          📍 ${escHtml(loc)} · ${escHtml(chars)}<br>
          <span style="font-size:11px">${msgs.length} 条消息</span>
        </div>
        <div class="collapse-header" onclick="toggleCollapse(this)">
          <span class="icon">${isOpen ? '▼' : '▶'}</span> 查看对话
        </div>
        <div class="collapse-body${isOpen ? ' open' : ''}">
          ${msgs.map(m => {
        const speaker = m.speaker || m.role || '?';
        const text = m.text || m.content || '';
        const time = m.time || '';
        const isUser = speaker === (STATE.player_character || '');
        const isSystem = speaker === 'system';
        const roleClass = isUser ? 'user' : isSystem ? 'system' : '';
        return `<div class="msg"><span class="role ${roleClass}">[${escHtml(time)}] ${escHtml(speaker)}</span><span class="content">${escHtml(text.substring(0, 500))}</span></div>`;
      }).join('')}
          ${msgs.length === 0 ? '<div class="empty">暂无消息</div>' : ''}
        </div>
      </div>`;
    });
    html += '</div>';
  }
  el.innerHTML = html;
  _lastSessionCount = data.active.reduce((sum, s) => sum + (s.messages || []).length, 0);
}

// 自动轮询：每 5 秒刷新会话和状态
let _lastSessionCount = 0;

function startAutoRefresh() {
  if (_sessionsTimer) return;
  _sessionsTimer = setInterval(async () => {
    if (_archiveMode !== 'current') return;
    try {
      STATE = await api('/api/state');
      // 更新顶部时间
      let timeStr = (STATE.current_time || '').replace('T', ' ').replace(/:\d{2}$/, '');
      let dow = getDayOfWeek(STATE.current_time);
      $('#headerTime').textContent = `🕐 ${timeStr} ${dow}`;

      // 只在会话数量变化时才重新渲染（避免折叠被重置）
      const tab = document.querySelector('.tab-btn.active')?.dataset.tab;
      if (tab === 'sessions') {
        const data = await api('/api/sessions');
        const totalMsgs = data.active.reduce((sum, s) => sum + (s.messages || []).length, 0);
        if (totalMsgs !== _lastSessionCount) {
          _lastSessionCount = totalMsgs;
          loadSessions();
        }
      }
    } catch (e) { /* ignore polling errors */ }
  }, 1000);
}

function stopAutoRefresh() {
  if (_sessionsTimer) { clearInterval(_sessionsTimer); _sessionsTimer = null; }
}
