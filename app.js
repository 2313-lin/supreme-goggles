/* 夜航 · 前端（原生 JS，无构建、无 CDN） */
(() => {
  'use strict';

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    tab: 'all',
    payload: null,
    userId: localStorage.getItem('nds_user') || 'u_demo',
    sessionId: localStorage.getItem('nds_session') || '',
    demoMode: false,
    skillQuery: '',
  };
  if (!state.sessionId) {
    state.sessionId = 's_' + Math.random().toString(36).slice(2, 10);
    localStorage.setItem('nds_session', state.sessionId);
  }

  /* --------------------------------------------------------------- helpers */
  const esc = (str) => String(str ?? '').replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));

  const escRe = (str) => str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

  function mark(text, needles) {
    const safe = esc(text || '');
    const list = (needles || [])
      .filter((n) => typeof n === 'string' && n.trim().length >= 2)
      .sort((a, b) => b.length - a.length)
      .slice(0, 6);
    if (!list.length) return safe;
    const re = new RegExp('(' + list.map((n) => escRe(esc(n))).join('|') + ')', 'gi');
    return safe.replace(re, '<mark>$1</mark>');
  }

  async function api(path, options = {}) {
    const headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    if (options.admin) {
      headers['X-Admin-Token'] = $('#adminToken') ? $('#adminToken').value.trim() : '';
    }
    const resp = await fetch(path, { ...options, headers });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      const err = new Error((data.error && data.error.message) || `请求失败（${resp.status}）`);
      err.payload = data;
      throw err;
    }
    return data;
  }

  let toastTimer = null;
  function toast(message) {
    const el = $('#toast');
    el.textContent = message;
    el.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.hidden = true; }, 2600);
  }

  const STATE_LABEL = {
    healthy: '正常', degraded: '降级', half_open: '半开', circuit_open: '熔断',
    unavailable: '不可用', disabled: '未接入', skipped: '跳过',
  };

  /* ------------------------------------------------------------- platform */
  async function loadHealth() {
    try {
      const data = await api('/api/health');
      const healthy = data.platforms_healthy;
      const total = data.platforms_total;
      const works = data.corpus.works;
      $('#healthText').textContent = `${works} 部作品 · ${healthy}/${total} 平台正常`;
      const dot = $('#healthPill .dot');
      dot.className = 'dot ' + (healthy === total ? 'ok' : healthy > 0 ? 'warn' : 'bad');
    } catch (err) {
      $('#healthText').textContent = '服务不可用';
      $('#healthPill .dot').className = 'dot bad';
    }
  }

  async function loadPlatforms() {
    const data = await api('/api/platforms');
    const list = $('#platformList');
    list.innerHTML = '';
    data.platforms.forEach((p) => {
      const dotClass = { healthy: 'ok', degraded: 'warn', half_open: 'warn', circuit_open: 'bad', unavailable: 'bad', disabled: 'off' }[p.state] || 'off';
      const healthClass = p.health_score >= 0.8 ? '' : p.health_score >= 0.4 ? 'low' : 'bad';
      const row = document.createElement('div');
      row.className = 'plat' + (p.state === 'healthy' && p.latency_ms ? ' used' : '') + (p.state !== 'healthy' && p.state !== 'disabled' ? ' failed' : '');
      row.innerHTML = `
        <span class="dot ${dotClass}"></span>
        <span class="plat-name">${esc(p.id)}</span>
        <span class="plat-role">${p.type === 'novel' ? '小说' : '短剧'}·${p.stage}级</span>
        <span class="health ${healthClass}"><i style="width:${Math.round(p.health_score * 100)}%"></i></span>
        <span class="plat-state">${STATE_LABEL[p.state] || p.state}</span>`;
      if (state.demoMode && p.state !== 'disabled') {
        const btn = document.createElement('button');
        btn.className = 'ghost';
        btn.textContent = p.forced_down ? '恢复' : '停用';
        btn.title = `模拟 ${p.id} ${p.forced_down ? '恢复' : '不可用'}`;
        btn.addEventListener('click', () => togglePlatform(p.id, !p.forced_down));
        row.appendChild(btn);
      }
      list.appendChild(row);
    });
  }

  async function togglePlatform(id, down) {
    try {
      await api(`/api/admin/platforms/${encodeURIComponent(id)}/simulate`, {
        method: 'POST',
        admin: true,
        body: JSON.stringify({ down, confirm: true }),
      });
      toast(`${id} 已${down ? '停用' : '恢复'}`);
      await Promise.all([loadPlatforms(), loadHealth()]);
    } catch (err) {
      toast(err.message);
    }
  }

  /* ---------------------------------------------------------------- memory */
  async function loadMemory() {
    try {
      const data = await api(`/api/memory?user_id=${encodeURIComponent(state.userId)}&session_id=${encodeURIComponent(state.sessionId)}`);
      const profile = data.profile || {};
      const themes = profile.preferred_themes || [];
      const recent = (data.session && data.session.recent_queries) || [];
      const blocked = profile.blocked_topics || [];
      const clicked = Object.keys(profile.clicked_works || {}).length;

      let html = '';
      html += `<div class="mem-block"><div class="mem-title">偏好主题</div><div class="mem-tags">${
        themes.length ? themes.map((t) => `<span class="mem-chip">${esc(t)}</span>`).join('') : '<span class="mem-chip">点击结果后逐渐形成</span>'
      }</div></div>`;
      html += `<div class="mem-block"><div class="mem-title">本次会话检索</div>${
        recent.length
          ? `<ul class="mem-list">${recent.slice(0, 5).map((q) => `<li>${esc(q)}</li>`).join('')}</ul>`
          : '<div class="mem-tags"><span class="mem-chip">还没有检索记录</span></div>'
      }</div>`;
      html += `<div class="mem-block"><div class="mem-title">已点击作品</div><div class="mem-tags"><span class="mem-chip">${clicked} 部</span></div></div>`;
      if (blocked.length) {
        html += `<div class="mem-block"><div class="mem-title">屏蔽主题</div><div class="mem-tags">${
          blocked.map((t) => `<span class="mem-chip">${esc(t)}</span>`).join('')
        }</div></div>`;
      }
      $('#memoryBox').innerHTML = html;
      $('#blockedInput').value = blocked.join('，');
    } catch (err) {
      $('#memoryBox').innerHTML = `<div class="mem-chip">记忆暂不可用</div>`;
    }
  }

  async function saveBlocked() {
    const raw = $('#blockedInput').value.trim();
    const topics = raw.split(/[,，、\s]+/).map((s) => s.trim()).filter(Boolean);
    try {
      await api('/api/memory/blocked', {
        method: 'POST',
        body: JSON.stringify({ user_id: state.userId, topics }),
      });
      toast(topics.length ? `已屏蔽 ${topics.length} 个主题` : '已清空屏蔽');
      await loadMemory();
    } catch (err) {
      toast(err.message);
    }
  }

  /* --------------------------------------------------------------- results */
  function notices(payload) {
    const out = [];
    const warnings = new Set(payload.warnings || []);
    if (payload.degraded) {
      const reasons = (payload.degraded_reasons || []).filter((r) => !warnings.has(r));
      out.push(`<div class="notice bad"><span class="icon">⚠</span><div><strong>部分平台不可用，结果可能不完整</strong>
        ${reasons.length ? `<ul>${reasons.map((r) => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}</div></div>`);
    }
    if ((payload.warnings || []).length) {
      out.push(`<div class="notice warn"><span class="icon">ℹ</span><div>${payload.warnings.map((r) => esc(r)).join('<br>')}</div></div>`);
    }
    if (payload.code === 'rate_limited') {
      out.push(`<div class="notice bad"><span class="icon">⏳</span><div>${esc(payload.message)}</div></div>`);
    }
    if ((payload.guardrail_flags || []).includes('injection_suspected')) {
      out.push(`<div class="notice info"><span class="icon">🛡</span><div>检测到疑似指令文本，已按普通检索内容处理。</div></div>`);
    }
    return out.join('');
  }

  function cardHtml(item, needles) {
    const work = item.work;
    const primary = work.sources.find((s) => s.platform === item.platform_primary) || work.sources[0] || {};
    const evidence = (item.evidence || []).map((ev) => {
      const cls = ev.kind === 'line' ? 'ev line' : ev.kind === 'character' ? 'ev character' : ev.kind === 'theme' ? 'ev theme' : 'ev';
      const kindLabel = { sentence: '原文句子', line: '台词', character: '角色', theme: '主题/标签', title: '标题' }[ev.kind] || ev.kind;
      return `<div class="${cls}">
          <div class="ev-kind"><span>${kindLabel}${ev.position ? ' · ' + esc(ev.position) : ''}</span>
            ${ev.verified ? '<span class="ok">✓ 已校验</span>' : ''}</div>
          <p class="ev-text">${mark(ev.text, [ev.matched_query, ...needles])}</p>
        </div>`;
    }).join('');

    const tags = (work.themes || []).slice(0, 6)
      .map((t) => `<span class="tag">${esc(t)}</span>`).join('');

    const alsoOn = (item.also_on || []).length
      ? `<span class="tag">其他来源：${item.also_on.map(esc).join(' / ')}</span>` : '';

    return `<article class="card" data-id="${esc(work.id)}">
      <div class="card-top">
        <div>
          <h3 class="card-title"><a href="#" data-detail="${esc(work.id)}">${esc(work.title)}</a></h3>
          <p class="card-sub">
            <span class="author">${esc(work.author || '佚名')}</span>
            <span>·</span><span>${esc(primary.platform || '—')}</span>
            ${work.year ? `<span>·</span><span>${work.year}</span>` : ''}
            ${work.episodes ? `<span>·</span><span>${work.episodes} 集</span>` : ''}
          </p>
        </div>
        <div class="badges">
          <span class="badge layer-${item.layer}">${esc(item.layer_label)}</span>
          <span class="badge conf-${item.confidence >= 0.85 ? 'high' : item.confidence >= 0.65 ? 'mid' : 'low'}">置信 ${esc(item.confidence_label)}</span>
          ${item.verified ? '<span class="badge verified">证据已校验</span>' : ''}
        </div>
      </div>
      <p class="summary">${esc(work.summary)}</p>
      <div class="evidence">${evidence}</div>
      ${tags || alsoOn ? `<div class="tags">${alsoOn}${tags}</div>` : ''}
      <div class="card-actions">
        <button class="btn" data-detail="${esc(work.id)}">查看详情</button>
        ${primary.url ? `<a class="btn ghost" href="${esc(primary.url)}" target="_blank" rel="noreferrer noopener">在 ${esc(primary.platform)} 打开</a>` : ''}
      </div>
    </article>`;
  }

  function render(payload) {
    state.payload = payload;
    const results = $('#results');
    if (payload.allowed === false) {
      results.innerHTML = `<div class="empty"><strong>${esc(payload.code === 'blocked_topic' ? '这个查询无法检索' : '暂时无法完成搜索')}</strong>
        <p>${esc(payload.message || '')}</p>
        ${(payload.suggestions || []).map((s) => `<p class="meta">· ${esc(s)}</p>`).join('')}</div>`;
      $('#tracePanel').hidden = true;
      loadMemory();
      return;
    }

    const u = payload.understanding || {};
    const entities = (u.entities || []).map((e) =>
      `<span class="intent-chip entity">${esc({ sentence: '句子', title: '标题', character: '角色', theme: '主题' }[e.kind] || e.kind)}：${esc(e.canonical || e.value)}</span>`
    ).join('');

    const head = `<div class="result-head">
      <span class="query">${esc(payload.query)}</span>
      <span class="sep">/</span>
      <span>${payload.groups ? Object.keys(payload.groups).length : 0} 组结果</span>
      <span>·</span><span>${payload.elapsed_ms} ms</span>
      <span>·</span><span>${payload.degraded ? '部分降级' : '正常'}</span>
      ${payload.expanded ? '<span class="intent-chip">已拓展备用源</span>' : ''}
      ${entities}
    </div>`;

    const groups = Object.entries(payload.groups || {}).map(([kind, group]) => {
      const body = group.results.length
        ? `<div class="cards">${group.results.map((item) => cardHtml(item, [payload.query, ...(u.sentence_queries || [])])).join('')}</div>`
        : `<div class="group-empty">这一类没有命中，可以换主角名、原文句子或主题试试。</div>`;
      return `<section class="group">
        <div class="group-head"><h2>${esc(group.label)}</h2><span class="count">${group.total} 条</span></div>
        ${body}
      </section>`;
    }).join('');

    results.innerHTML = head + notices(payload) + groups;
    renderTrace(payload);
    loadMemory();
  }

  function renderTrace(payload) {
    const u = payload.understanding || {};
    const used = (payload.platforms || []).filter((p) => p.used);
    const rows = [
      ['意图', `${u.intent || '—'} → ${(u.types || []).join(' / ')}`],
      ['实体', (u.entities || []).map((e) => `${e.kind}:${e.canonical || e.value}`).join('；') || '无'],
      ['改写', (u.rewrites || []).slice(0, 3).join(' | ') || '—'],
      ['预算', `${payload.elapsed_ms} / ${payload.budget_ms} ms`],
      ['降级层级', String(payload.fallback_stage)],
      ['参与平台', used.map((p) => `${p.platform}(${p.latency_ms}ms)`).join('、') || '无'],
    ];
    $('#traceBody').innerHTML = `<dl>${rows.map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')}</dl>`;
    $('#tracePanel').hidden = false;
  }

  /* ----------------------------------------------------------------- modal */
  async function openDetail(workId) {
    try {
      const data = await api(`/api/works/${encodeURIComponent(workId)}`);
      const work = data.work;
      api('/api/feedback/click', {
        method: 'POST',
        body: JSON.stringify({ user_id: state.userId, session_id: state.sessionId, work_id: work.id }),
      }).then(loadMemory).catch(() => {});

      const sentences = (work.sentences || []).map((s) => {
        const kind = s.type === 'subtitle' ? '台词' : s.type === 'dialogue' ? '对白' : '叙述';
        const hasClock = /[:：]\s*\d{2}/.test(s.position || '');
        const clock = s.start_time != null && !hasClock
          ? ` · ${formatTime(s.start_time)}-${formatTime(s.end_time)}` : '';
        return `<div class="quote"><div class="meta">${kind}${s.position ? ' · ' + esc(s.position) : ''}${clock}</div>${esc(s.text)}</div>`;
      }).join('');

      const chars = (work.characters || []).map((c) =>
        `<li><b>${esc(c.name)}</b> <span class="meta">${esc(c.role)} · 重要度 ${c.importance_score}</span><br>${esc(c.description)}</li>`
      ).join('');

      const sources = (work.sources || []).map((s) =>
        `<li>${esc(s.platform)} <span class="meta">${esc(s.tier)} · ${esc(s.content_level)}</span>${
          s.url ? ` · <a href="${esc(s.url)}" target="_blank" rel="noreferrer noopener">打开</a>` : ''
        }</li>`
      ).join('');

      $('#modalBody').innerHTML = `
        <h2 id="modalTitle">${esc(work.title)}</h2>
        <p class="sub">${esc(work.author || '佚名')} · ${work.type === 'novel' ? '短篇小说' : '短剧'}${
          work.year ? ' · ' + work.year : ''} · ${esc((work.themes || []).join(' / '))}</p>
        <p>${esc(work.summary)}</p>
        <h4>角色</h4><ul>${chars || '<li>暂无角色信息</li>'}</ul>
        <h4>原文句子 / 台词</h4>${sentences || '<p class="meta">暂无内容层数据（该平台仅提供 L1 搜索层）</p>'}
        <h4>来源</h4><ul>${sources}</ul>`;
      $('#modal').hidden = false;
    } catch (err) {
      toast(err.message);
    }
  }

  const formatTime = (sec) => {
    if (sec == null) return '';
    const m = Math.floor(sec / 60);
    const s = Math.floor(sec % 60);
    return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  };

  /* ---------------------------------------------------------------- search */
  async function search(query) {
    const q = (query || $('#query').value || '').trim();
    if (!q) { toast('请输入主角名称、原文句子或主题'); return; }
    $('#query').value = q;
    try {
      const url = new URL(location.href);
      url.searchParams.set('q', q);
      url.searchParams.set('tab', state.tab);
      history.replaceState(null, '', url);
    } catch (err) { /* 忽略 */ }
    const btn = $('#searchBtn');
    btn.disabled = true;
    btn.textContent = '检索中…';
    $('#results').innerHTML = `<div class="empty"><strong>正在多路召回</strong><p>句子 / 角色 / 主题 / 剧名 / 台词通道并行检索…</p></div>`;
    try {
      const payload = await api('/api/search', {
        method: 'POST',
        body: JSON.stringify({
          query: q, tab: state.tab, user_id: state.userId, session_id: state.sessionId,
        }),
      });
      render(payload);
      loadPlatforms().catch(() => {});
    } catch (err) {
      $('#results').innerHTML = `<div class="empty"><strong>搜索失败</strong><p>${esc(err.message)}</p></div>`;
    } finally {
      btn.disabled = false;
      btn.textContent = '搜索';
    }
  }

  async function loadExamples() {
    try {
      const data = await api('/api/examples');
      const box = $('#examples');
      box.innerHTML = '';
      data.examples.forEach((ex) => {
        const btn = document.createElement('button');
        btn.className = 'chip';
        btn.textContent = ex.hint;
        btn.title = ex.query;
        btn.addEventListener('click', () => {
          state.tab = ex.tab === 'all' ? 'all' : ex.tab;
          $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === state.tab));
          $('#query').value = ex.query;
          search(ex.query);
        });
        box.appendChild(btn);
      });
    } catch (err) { /* 忽略示例加载失败 */ }
  }

  /* ------------------------------------------------------------------- init */
  function bind() {
    $('#searchForm').addEventListener('submit', (e) => { e.preventDefault(); search(); });
    $$('.tab').forEach((tab) => tab.addEventListener('click', () => {
      state.tab = tab.dataset.tab;
      $$('.tab').forEach((t) => t.classList.toggle('active', t === tab));
    }));
    $('#refreshPlatforms').addEventListener('click', () => loadPlatforms().catch((e) => toast(e.message)));
    $('#demoMode').addEventListener('change', (e) => {
      state.demoMode = e.target.checked;
      $('#adminToken').parentElement.classList.toggle('on', state.demoMode);
      loadPlatforms().catch(() => {});
    });
    $('#saveBlocked').addEventListener('click', saveBlocked);
    $('#results').addEventListener('click', (e) => {
      const target = e.target.closest('[data-detail]');
      if (target) { e.preventDefault(); openDetail(target.dataset.detail); }
    });
    $('#modalClose').addEventListener('click', () => { $('#modal').hidden = true; });
    $('#modal').addEventListener('click', (e) => { if (e.target === $('#modal')) $('#modal').hidden = true; });
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') $('#modal').hidden = true;
      if (e.key === '/' && document.activeElement !== $('#query')) {
        e.preventDefault();
        $('#query').focus();
      }
    });
  }

  async function init() {
    bind();
    await Promise.all([loadHealth(), loadPlatforms().catch(() => {}), loadMemory(), loadExamples()]);
    const params = new URLSearchParams(location.search);
    const tab = params.get('tab');
    if (['all', 'novel', 'drama'].includes(tab)) {
      state.tab = tab;
      $$('.tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === tab));
    }
    const query = params.get('q');
    if (query) {
      $('#query').value = query;
      search(query);
    } else {
      $('#query').focus();
    }
    const workId = params.get('work');
    if (workId) openDetail(workId);
  }

  init();
})();
