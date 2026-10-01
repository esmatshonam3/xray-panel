/* ==========================================================================
   Xray Panel — SPA
   • hash router, no build step, no hard CDN dependency (fonts degrade gracefully)
   • bilingual (fa/en) with automatic RTL, dark/light theme
   • animated counters, charts, staggered lists, skeleton loaders, Ctrl+K palette
   ========================================================================== */
(() => {
  'use strict';

  const { t, num, date, bytes, relative } = window.I18N;
  const API = '/api/v1';
  const TOKEN_KEY = 'xpanel.access';
  const REFRESH_KEY = 'xpanel.refresh';
  const THEME_KEY = 'xpanel.theme';
  const STAFF_ROLES = ['support', 'admin', 'owner'];

  const state = {
    token: localStorage.getItem(TOKEN_KEY) || '',
    refresh: localStorage.getItem(REFRESH_KEY) || '',
    me: null,
    page: 'dashboard',
    paging: {
      configs: { page: 1, size: 20, total: 0 },
      users: { page: 1, size: 20, total: 0 },
    },
    selected: new Set(),
    paletteIndex: 0,
    paletteItems: [],
  };

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const esc = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
  const isStaff = () => !!state.me && STAFF_ROLES.includes(state.me.role);

  /* ==================================================================== theme */
  const Theme = {
    get() { return document.documentElement.getAttribute('data-theme') || 'dark'; },
    set(mode, { persist = true } = {}) {
      document.documentElement.setAttribute('data-theme', mode);
      if (persist) localStorage.setItem(THEME_KEY, mode);
      const meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute('content', mode === 'dark' ? '#070a12' : '#f4f6fb');
      Theme.paint();
    },
    toggle() { Theme.set(Theme.get() === 'dark' ? 'light' : 'dark'); },
    paint() {
      const dark = Theme.get() === 'dark';
      const btn = $('#theme-icon');
      if (btn) btn.innerHTML = window.icon(dark ? 'moon' : 'sun');
      const label = $('#login-theme-label');
      if (label) label.textContent = dark ? t('theme.dark') : t('theme.light');
    },
  };

  /* ===================================================================== i18n */
  function applyStaticI18n(root = document) {
    root.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
    root.querySelectorAll('[data-i18n-placeholder]').forEach((el) => { el.placeholder = t(el.dataset.i18nPlaceholder); });
    root.querySelectorAll('[data-i18n-title]').forEach((el) => { el.title = t(el.dataset.i18nTitle); });
    const langBtn = $('#lang-label');
    if (langBtn) langBtn.textContent = window.I18N.lang === 'fa' ? 'EN' : 'FA';
    const loginLang = $('#login-lang-label');
    if (loginLang) loginLang.textContent = window.I18N.lang === 'fa' ? 'EN' : 'FA';
    Theme.paint();
  }

  /* ====================================================================== api */
  async function api(path, options = {}) {
    const opts = { ...options, headers: { ...(options.headers || {}) } };
    if (state.token) opts.headers.Authorization = `Bearer ${state.token}`;
    if (opts.body && typeof opts.body !== 'string') {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(opts.body);
    }
    const res = await fetch(path.startsWith('http') ? path : API + path, opts);

    if (res.status === 401 && state.refresh && !path.includes('/auth/refresh') && !path.startsWith('/auth/')) {
      if (await tryRefresh()) return api(path, options);
      logout(t('login.sessionExpired'));
    }
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    if (!res.ok) {
      const detail = data && data.detail;
      const err = new Error(typeof detail === 'string' ? detail : t('error.generic'));
      err.status = res.status;
      throw err;
    }
    return data;
  }

  async function tryRefresh() {
    try {
      const res = await fetch(`${API}/auth/refresh`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: state.refresh }),
      });
      if (!res.ok) return false;
      const data = await res.json();
      setTokens(data.access_token, data.refresh_token);
      return true;
    } catch { return false; }
  }

  function setTokens(access, refresh) {
    state.token = access || '';
    if (refresh) state.refresh = refresh;
    localStorage.setItem(TOKEN_KEY, state.token);
    localStorage.setItem(REFRESH_KEY, state.refresh);
  }

  /* ==================================================================== toasts */
  const TOAST_ICON = { ok: 'check', err: 'warning', warn: 'warning', info: 'info' };
  const TOAST_TITLE = { ok: 'status.ok', err: 'status.critical', warn: 'status.warning', info: 'status.info' };

  function toast(message, kind = 'info', title) {
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = `<span class="ico">${window.icon(TOAST_ICON[kind] || 'info', { size: 17 })}</span>
      <div class="body"><b>${esc(title || t(TOAST_TITLE[kind] || 'status.info'))}</b>
      <span>${esc(message)}</span></div>`;
    $('#toasts').appendChild(el);
    setTimeout(() => {
      el.classList.add('leaving');
      setTimeout(() => el.remove(), 240);
    }, 4200);
  }

  /* ===================================================================== modal */
  function openModal(title, bodyHtml, buttons = [], { wide = false } = {}) {
    $('#modal-title').textContent = title;
    $('#modal-body').innerHTML = bodyHtml;
    $('.modal').classList.toggle('wide', wide);
    const footer = $('#modal-footer');
    footer.innerHTML = '';
    buttons.forEach((btn) => {
      const el = document.createElement('button');
      el.className = `btn ${btn.kind || ''}`;
      el.innerHTML = (btn.icon ? `<span class="ico">${window.icon(btn.icon, { size: 15 })}</span>` : '') + esc(btn.label);
      el.onclick = () => btn.onClick?.(el);
      footer.appendChild(el);
    });
    window.hydrateIcons($('#modal'));
    $('#modal').classList.add('open');
  }
  const closeModal = () => $('#modal').classList.remove('open');

  /* ==================================================================== helpers */
  function badge(value, { dot = true } = {}) {
    const v = String(value ?? 'unknown').toLowerCase();
    const key = `status.${v}`;
    const label = t(key);
    return `<span class="badge ${esc(v)}${dot ? '' : ' no-dot'}">${esc(label === key ? v : label)}</span>`;
  }

  function usageCell(used, limit, percent) {
    const pct = limit ? Math.min(Number(percent) || 0, 100) : 0;
    const cls = !limit ? 'bar ok' : pct >= 90 ? 'bar warn' : 'bar';
    const label = limit ? `${bytes(used)} / ${bytes(limit)}` : `${bytes(used)} / ∞`;
    return `<div class="usage-cell">
      <span class="txt">${esc(label)}${limit ? ` · ${num(pct, { maximumFractionDigits: 0 })}%` : ''}</span>
      <span class="${cls}"><i style="width:${limit ? pct : 100}%"></i></span></div>`;
  }

  function emptyState(iconName, title, hint) {
    return `<div class="empty"><span class="ico">${window.icon(iconName, { size: 30, stroke: 1.4 })}</span>
      <h4>${esc(title)}</h4><p>${esc(hint || '')}</p></div>`;
  }

  function skeletonRows(rows = 6) {
    return `<tbody><tr><td style="padding:0"><div style="padding:16px">${Array.from({ length: rows }, () => '<div class="skeleton sk-row"></div>').join('')}</div></td></tr></tbody>`;
  }

  function animateValue(el, to, formatter) {
    const target = Number(to) || 0;
    const dur = 700;
    const start = performance.now();
    (function step(now) {
      const p = Math.min((now - start) / dur, 1);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = formatter(target * eased);
      if (p < 1) requestAnimationFrame(step);
    })(start);
  }

  function copyText(text, btn) {
    const done = () => {
      toast(t('common.copied'), 'ok');
      if (btn) {
        const original = btn.innerHTML;
        btn.innerHTML = `<span class="ico">${window.icon('check', { size: 15 })}</span>`;
        setTimeout(() => { btn.innerHTML = original; }, 1400);
      }
    };
    const fallback = () => {
      const ta = document.createElement('textarea');
      ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      try { document.execCommand('copy'); done(); } catch { /* ignore */ }
      ta.remove();
    };
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(text).then(done).catch(fallback);
    } else fallback();
  }

  function bindCopyButtons(root) {
    root.querySelectorAll('[data-copy-target]').forEach((btn) => {
      btn.onclick = () => {
        const input = document.getElementById(btn.dataset.copyTarget);
        if (input) copyText(input.value, btn);
      };
    });
  }

  function debounce(fn, ms) {
    let timer;
    return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), ms); };
  }

  /* ===================================================================== charts */
  function cssVar(name, fallback) {
    const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
    return v || fallback;
  }

  function drawLineChart(canvas, points, key = 'total_bytes', { animate = true } = {}) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 640;
    const h = canvas.clientHeight || 210;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const accent = cssVar('--accent', '#6366f1');
    const gridLine = cssVar('--grid-line', 'rgba(255,255,255,.06)');
    const mute = cssVar('--text-mute', '#66748f');
    const values = (points || []).map((p) => Number(p[key]) || 0);
    const max = Math.max(...values, 1);
    const padL = 54, padR = 12, padT = 14, padB = 24;
    const cw = w - padL - padR;
    const ch = h - padT - padB;

    const paint = (progress) => {
      ctx.clearRect(0, 0, w, h);
      ctx.font = '10px system-ui, sans-serif';
      ctx.strokeStyle = gridLine; ctx.lineWidth = 1;
      ctx.fillStyle = mute;
      for (let i = 0; i <= 4; i += 1) {
        const y = padT + (ch / 4) * i;
        ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(padL + cw, y); ctx.stroke();
        ctx.fillText(bytes(max - (max / 4) * i), 4, y + 3);
      }
      if (!values.length) {
        ctx.fillText(t('chart.noData'), padL + cw / 2 - 40, padT + ch / 2);
        return;
      }
      const stepX = values.length > 1 ? cw / (values.length - 1) : cw;
      const xy = values.map((v, i) => [padL + i * stepX, padT + ch - (v / max) * ch]);
      const count = Math.max(Math.floor(xy.length * progress), 2);
      const slice = xy.slice(0, count);

      const grad = ctx.createLinearGradient(0, padT, 0, padT + ch);
      grad.addColorStop(0, 'rgba(99,102,241,.38)');
      grad.addColorStop(1, 'rgba(99,102,241,0)');
      ctx.beginPath();
      ctx.moveTo(slice[0][0], padT + ch);
      slice.forEach(([x, y]) => ctx.lineTo(x, y));
      ctx.lineTo(slice[slice.length - 1][0], padT + ch);
      ctx.closePath(); ctx.fillStyle = grad; ctx.fill();

      ctx.beginPath();
      slice.forEach(([x, y], i) => (i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)));
      ctx.strokeStyle = accent; ctx.lineWidth = 2.2;
      ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      ctx.stroke();

      const last = slice[slice.length - 1];
      ctx.beginPath(); ctx.arc(last[0], last[1], 3.6, 0, Math.PI * 2);
      ctx.fillStyle = accent; ctx.fill();
      ctx.beginPath(); ctx.arc(last[0], last[1], 7.5, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(99,102,241,.2)'; ctx.fill();
    };

    if (!animate) { paint(1); return; }
    const start = performance.now();
    (function frame(now) {
      const p = Math.min((now - start) / 620, 1);
      paint(1 - Math.pow(1 - p, 3));
      if (p < 1) requestAnimationFrame(frame);
    })(start);
  }

  function drawBarChart(canvas, rows, { animate = true } = {}) {
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || 640;
    const h = canvas.clientHeight || 210;
    canvas.width = w * dpr; canvas.height = h * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const accent2 = cssVar('--accent-2', '#8b5cf6');
    const mute = cssVar('--text-mute', '#66748f');
    const padL = 46, padB = 24, padT = 12;
    const cw = w - padL - 12;
    const ch = h - padT - padB;

    if (!rows?.length) {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = mute; ctx.font = '11px system-ui';
      ctx.fillText(t('chart.noData'), padL, padT + ch / 2);
      return;
    }
    const max = Math.max(...rows.map((r) => r.amount), 1);
    const barW = Math.max(cw / rows.length - 6, 4);

    const paint = (progress) => {
      ctx.clearRect(0, 0, w, h);
      ctx.fillStyle = mute; ctx.font = '10px system-ui';
      for (let i = 0; i <= 3; i += 1) {
        const y = padT + (ch / 3) * i;
        ctx.fillText(num(Math.round(max - (max / 3) * i)), 4, y + 3);
      }
      rows.forEach((row, i) => {
        const bh = Math.max((row.amount / max) * ch * progress, 0);
        const x = padL + i * (cw / rows.length) + 3;
        const grad = ctx.createLinearGradient(0, padT + ch - bh, 0, padT + ch);
        grad.addColorStop(0, accent2);
        grad.addColorStop(1, 'rgba(139,92,246,.35)');
        ctx.fillStyle = grad;
        ctx.beginPath();
        if (ctx.roundRect) ctx.roundRect(x, padT + ch - bh, barW, bh, [4, 4, 0, 0]);
        else ctx.rect(x, padT + ch - bh, barW, bh);
        ctx.fill();
      });
      ctx.fillStyle = mute;
      ctx.fillText(String(rows[0]?.day || '').slice(5), padL, h - 6);
      ctx.fillText(String(rows[rows.length - 1]?.day || '').slice(5), padL + cw - 26, h - 6);
    };

    if (!animate) { paint(1); return; }
    const start = performance.now();
    (function frame(now) {
      const p = Math.min((now - start) / 620, 1);
      paint(1 - Math.pow(1 - p, 3));
      if (p < 1) requestAnimationFrame(frame);
    })(start);
  }

  /* ====================================================================== pages */
  const Pages = {};

  Pages.dashboard = async ({ silent } = {}) => {
    if (!silent) {
      $('#stat-cards').innerHTML = Array.from({ length: 4 }, () => '<div class="skeleton sk-card"></div>').join('');
    }
    const [stats, traffic, top, alerts, nodes] = await Promise.all([
      api('/reports/dashboard'),
      api('/reports/traffic?days=30'),
      api('/reports/top-consumers?limit=6'),
      isStaff() ? api('/alerts?limit=5').catch(() => []) : Promise.resolve([]),
      api('/nodes?size=8').catch(() => ({ items: [] })),
    ]);

    const cards = [
      { icon: 'users', tone: 'info', label: t('dashboard.users'), value: stats.users_total,
        sub: t('dashboard.usersSub', { active: num(stats.users_active), today: num(stats.users_new_today) }),
        fmt: (v) => num(Math.round(v)) },
      { icon: 'layers', tone: '', label: t('dashboard.configs'), value: stats.services_total,
        sub: t('dashboard.configsSub', { active: num(stats.services_active), expiring: num(stats.services_expiring_7d) }),
        fmt: (v) => num(Math.round(v)) },
      { icon: 'server', tone: stats.nodes_total && stats.nodes_online === stats.nodes_total ? 'ok' : 'warn',
        label: t('dashboard.nodes'), value: stats.nodes_online,
        sub: t('dashboard.nodesSub', { online: num(stats.nodes_online), total: num(stats.nodes_total) }),
        fmt: (v) => `${num(Math.round(v))}/${num(stats.nodes_total)}` },
      { icon: 'wifi', tone: 'info', label: t('dashboard.trafficToday'), value: stats.traffic_today_bytes,
        sub: `${t('dashboard.traffic30')}: ${bytes(stats.traffic_month_bytes)}`, fmt: (v) => bytes(v) },
      { icon: 'wallet', tone: 'ok', label: t('dashboard.revenue'), value: stats.revenue_month,
        sub: t('dashboard.revenueSub', { n: num(stats.pending_payments) }),
        fmt: (v) => `${num(v, { maximumFractionDigits: 2 })} ${stats.currency}` },
      { icon: 'bell', tone: stats.alerts_active ? 'danger' : 'ok', label: t('dashboard.alerts'),
        value: stats.alerts_active, sub: t('dashboard.alertsSub'), fmt: (v) => num(Math.round(v)) },
    ];

    $('#stat-cards').innerHTML = cards.map((c, i) => `
      <div class="stat" style="--i:${i}">
        <div class="stat-top">
          <span class="stat-ico ${c.tone}">${window.icon(c.icon, { size: 17 })}</span>
          <span class="label">${esc(c.label)}</span>
        </div>
        <div class="value">0</div>
        <div class="sub">${esc(c.sub)}</div>
      </div>`).join('');

    $$('#stat-cards .value').forEach((el, i) => animateValue(el, cards[i].value, cards[i].fmt));

    drawLineChart($('#chart-traffic'), traffic, 'total_bytes', { animate: !silent });

    $('#top-consumers').innerHTML = top.length
      ? top.map((row) => `<div class="list-row">
          <div class="grow"><div class="title">${esc(row.label)}</div>
          <div class="meta">${esc(row.username)}</div></div>
          <div style="min-width:150px">${usageCell(row.used_bytes, row.limit_bytes, row.usage_percent)}</div>
        </div>`).join('')
      : emptyState('users', t('dashboard.noUsage'));

    $('#dash-alerts').innerHTML = alerts.length
      ? alerts.map((a) => `<div class="list-row">
          ${badge(a.level)}
          <div class="grow"><div class="title">${esc(a.title)}</div>
          <div class="meta mono">${esc(a.code)} · ×${num(a.occurrences)}</div></div>
        </div>`).join('')
      : emptyState('check', t('dashboard.noAlerts'));

    $('#dash-nodes').innerHTML = nodes.items?.length
      ? nodes.items.map((n) => `<div class="list-row">
          ${badge(n.status)}
          <div class="grow"><div class="title">${esc(n.name)}</div>
          <div class="meta">${esc(n.region || '—')} · ${num(n.service_count)} ${t('users.configs')}</div></div>
          <div style="text-align:end;min-width:74px">
            <div class="mono" style="font-size:11.5px">CPU ${num(Math.round(n.cpu_percent || 0))}%</div>
            <div class="bar" style="margin-block-start:4px"><i style="width:${Math.min(n.cpu_percent || 0, 100)}%"></i></div>
          </div>
        </div>`).join('')
      : emptyState('server', t('dashboard.noNodes'));
  };

  /* --------------------------------------------------------------- configs */
  Pages.configs = async () => {
    const ps = state.paging.configs;
    const params = new URLSearchParams({ page: ps.page, size: ps.size });
    const q = $('#svc-search').value.trim();
    if (q) params.set('q', q);
    if ($('#svc-status').value) params.set('status_filter', $('#svc-status').value);

    const table = $('#svc-table');
    table.innerHTML = skeletonRows(5);
    const data = await api(`/services?${params}`);
    ps.total = data.total;
    const staff = isStaff();
    state.selected.clear();
    renderBulkBar();

    if (!data.items.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState('layers', t('common.empty'), t('common.emptyHint'))}</td></tr></tbody>`;
      $('#svc-count').textContent = '';
      return;
    }

    table.innerHTML = `
      <thead><tr>
        ${staff ? '<th style="width:38px"><input type="checkbox" id="svc-all"></th>' : ''}
        <th>${t('configs.label')}</th><th>${t('configs.user')}</th><th>${t('common.protocol')}</th>
        <th>${t('common.node')}</th><th>${t('common.status')}</th><th>${t('common.usage')}</th>
        <th>${t('common.expires')}</th><th style="width:190px">${t('common.actions')}</th>
      </tr></thead>
      <tbody>${data.items.map((s) => `
        <tr data-id="${s.id}">
          ${staff ? `<td><input type="checkbox" class="svc-pick" value="${s.id}"></td>` : ''}
          <td><b>${esc(s.label)}</b><div class="sub mono">#${s.id}</div></td>
          <td>${esc(s.username || '—')}</td>
          <td><span class="chip mono">${esc(s.protocol)}</span></td>
          <td>${esc(s.node_name || '—')}<div class="sub">${esc(s.inbound_tag || '')}</div></td>
          <td>${badge(s.status)}${s.is_synced ? '' : `<div class="sub" style="color:var(--warn)">${t('configs.notSynced')}</div>`}</td>
          <td>${usageCell(s.used_bytes, s.traffic_limit_bytes, s.usage_percent)}</td>
          <td>${s.expires_at ? `${date(s.expires_at)}<div class="sub">${s.days_left != null ? t('common.daysLeft', { n: num(s.days_left) }) : ''}</div>` : '∞'}</td>
          <td><div class="cell-actions">
            <button class="btn sm" data-act="view" data-id="${s.id}">${window.icon('link', { size: 14 })}<span>${t('configs.link')}</span></button>
            ${staff ? `<button class="btn sm" data-act="renew" data-id="${s.id}">${window.icon('refresh', { size: 14 })}</button>
                       <button class="btn sm danger" data-act="del" data-id="${s.id}">${window.icon('trash', { size: 14 })}</button>` : ''}
          </div></td>
        </tr>`).join('')}</tbody>`;

    $('#svc-count').textContent = `${num(data.total)} · ${t('common.page')} ${num(data.page)}/${num(data.pages)}`;
    window.hydrateIcons(table);
    bindServiceActions();
  };

  function bindServiceActions() {
    $$('#svc-table [data-act]').forEach((btn) => {
      btn.onclick = async () => {
        const id = Number(btn.dataset.id);
        try {
          if (btn.dataset.act === 'view') return showServiceDetail(id);
          if (btn.dataset.act === 'renew') return renewDialog(id);
          if (btn.dataset.act === 'del') {
            if (!confirm(t('configs.deleteConfirm'))) return;
            await api(`/services/${id}`, { method: 'DELETE' });
            toast(t('configs.deleted'), 'ok');
            return Pages.configs();
          }
        } catch (err) { toast(err.message, 'err'); }
      };
    });

    const all = $('#svc-all');
    if (all) {
      all.onchange = () => {
        $$('.svc-pick').forEach((c) => {
          c.checked = all.checked;
          c.closest('tr').classList.toggle('selected', all.checked);
          if (all.checked) state.selected.add(Number(c.value)); else state.selected.delete(Number(c.value));
        });
        renderBulkBar();
      };
    }
    $$('.svc-pick').forEach((cb) => {
      cb.onchange = () => {
        const id = Number(cb.value);
        cb.closest('tr').classList.toggle('selected', cb.checked);
        if (cb.checked) state.selected.add(id); else state.selected.delete(id);
        renderBulkBar();
      };
    });
  }

  function renderBulkBar() {
    const n = state.selected.size;
    $('#svc-bulkbar').innerHTML = n
      ? `<div class="bulk-bar"><span class="ico">${window.icon('check', { size: 16 })}</span>
         <span>${esc(t('common.selected', { n: num(n) }))}</span></div>`
      : '';
  }

  async function showServiceDetail(id) {
    const s = await api(`/services/${id}`);
    const subToken = s.subscription_url ? s.subscription_url.split('/').pop() : '';
    openModal(t('configs.detailTitle', { id: num(s.id), label: s.label }), `
      <div class="grid cols-2" style="gap:16px">
        <div>
          <dl class="kv">
            <dt>${t('common.status')}</dt><dd>${badge(s.status)}</dd>
            <dt>${t('common.protocol')}</dt><dd class="mono">${esc(s.protocol)}</dd>
            <dt>${t('common.node')}</dt><dd>${esc(s.node_name)} / ${esc(s.inbound_tag)}</dd>
            <dt>${t('common.expires')}</dt><dd>${date(s.expires_at)} ${s.days_left != null ? `(${t('common.daysLeft', { n: num(s.days_left) })})` : ''}</dd>
            <dt>${t('common.usage')}</dt><dd>${bytes(s.used_bytes)} / ${s.traffic_limit_bytes ? bytes(s.traffic_limit_bytes) : t('common.unlimited')}</dd>
          </dl>
        </div>
        <div style="text-align:center">
          <div style="background:#fff;padding:12px;border-radius:14px;display:inline-block">
            <img alt="QR" style="width:186px;height:186px;display:block" src="/api/v1/subscription/${esc(subToken)}/qr.png">
          </div>
          <div class="sub" style="margin-block-start:8px;color:var(--text-mute);font-size:11.5px">${esc(t('configs.qrHint'))}</div>
        </div>
      </div>
      <label class="field">${t('configs.connectionUri')}</label>
      <div class="copy-box">
        <input readonly id="cp-link" value="${esc(s.raw_link || '')}">
        <button class="btn sm" data-copy-target="cp-link">${window.icon('copy', { size: 14 })}</button>
      </div>
      <label class="field">${t('configs.subscriptionUrl')}</label>
      <div class="copy-box">
        <input readonly id="cp-sub" value="${esc(s.subscription_url || '')}">
        <button class="btn sm" data-copy-target="cp-sub">${window.icon('copy', { size: 14 })}</button>
      </div>
      <label class="field">${t('configs.clashProfile')}</label>
      <div class="copy-box">
        <input readonly id="cp-clash" value="${esc(s.subscription_url || '')}?format=clash">
        <button class="btn sm" data-copy-target="cp-clash">${window.icon('copy', { size: 14 })}</button>
      </div>`,
      [{ label: t('common.close'), onClick: closeModal }], { wide: true });
    bindCopyButtons($('#modal-body'));
  }

  function renewDialog(id) {
    openModal(t('configs.renewTitle', { id: num(id) }), `
      <div class="field-row">
        <div><label class="field">${t('configs.daysToAdd')}</label><input id="rn-days" type="number" value="30" min="1"></div>
        <div><label class="field">${t('configs.extraTraffic')}</label><input id="rn-gb" type="number" value="0" min="0"></div>
      </div>
      <label class="switch" style="margin-block-start:14px">
        <input type="checkbox" id="rn-reset"><span class="track"></span>
        <span>${t('configs.resetTraffic')}</span>
      </label>`,
      [
        { label: t('common.cancel'), onClick: closeModal },
        {
          label: t('configs.renew'), kind: 'primary', icon: 'refresh',
          onClick: async (btn) => {
            btn.classList.add('loading');
            try {
              await api(`/services/${id}/renew`, {
                method: 'POST',
                body: {
                  days: Number($('#rn-days').value),
                  add_traffic_gb: Number($('#rn-gb').value),
                  reset_traffic: $('#rn-reset').checked,
                },
              });
              closeModal(); toast(t('configs.renewed'), 'ok'); Pages.configs();
            } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
          },
        },
      ]);
  }

  function createServiceDialog() {
    openModal(t('configs.createTitle'), `
      <div class="field-row">
        <div><label class="field">${t('configs.user')}</label><select id="cs-user"><option value="">${t('common.loading')}</option></select></div>
        <div><label class="field">${t('plans.title')} <span class="hint">${t('common.optional')}</span></label><select id="cs-plan"><option value="">${t('common.auto')}</option></select></div>
        <div><label class="field">${t('inbounds.title')} <span class="hint">${t('common.optional')}</span></label><select id="cs-inbound"><option value="">${t('common.auto')}</option></select></div>
        <div><label class="field">${t('configs.label')}</label><input id="cs-label" placeholder="config"></div>
        <div><label class="field">${t('configs.durationDays')}</label><input id="cs-days" type="number"></div>
        <div><label class="field">${t('configs.trafficGb')}</label><input id="cs-gb" type="number"></div>
      </div>
      <p id="cs-prereq" style="color:var(--text-mute);font-size:12px;margin-block-start:12px">${esc(t('configs.autoSelectHint'))}</p>`,
      [
        { label: t('common.cancel'), onClick: closeModal },
        {
          label: t('common.create'), kind: 'primary', icon: 'plus',
          onClick: async (btn) => {
            if (!$('#cs-user').value) { toast(t('configs.selectUser'), 'err'); return; }
            btn.classList.add('loading');
            try {
              const s = await api('/services', {
                method: 'POST',
                body: {
                  user_id: Number($('#cs-user').value),
                  plan_id: $('#cs-plan').value ? Number($('#cs-plan').value) : null,
                  inbound_id: $('#cs-inbound').value ? Number($('#cs-inbound').value) : null,
                  label: $('#cs-label').value || null,
                  duration_days: $('#cs-days').value ? Number($('#cs-days').value) : null,
                  traffic_gb: $('#cs-gb').value ? Number($('#cs-gb').value) : null,
                },
              });
              closeModal(); toast(t('configs.created'), 'ok'); Pages.configs(); showServiceDetail(s.id);
            } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
          },
        },
      ]);
    Promise.all([api('/users?size=100'), api('/plans?size=100&include_inactive=true'), api('/inbounds')]).then(([users, plans, inbounds]) => {
      const userSelect = $('#cs-user');
      const planSelect = $('#cs-plan');
      const inboundSelect = $('#cs-inbound');
      if (!userSelect) return;
      users.items.forEach((u) => userSelect.insertAdjacentHTML('beforeend', `<option value="${u.id}">${esc(u.username)} · #${u.id}</option>`));
      plans.items.forEach((p) => planSelect.insertAdjacentHTML('beforeend', `<option value="${p.id}">${esc(p.name)} · #${p.id}</option>`));
      inbounds.forEach((i) => inboundSelect.insertAdjacentHTML('beforeend', `<option value="${i.id}">${esc(i.remark || i.tag)} · #${i.id}</option>`));
      if (!users.items.length) $('#cs-prereq').textContent = t('configs.noUsers');
      else if (!inbounds.length) $('#cs-prereq').textContent = t('configs.noInbounds');
    }).catch((err) => { const hint = $('#cs-prereq'); if (hint) hint.textContent = err.message; });
  }

  /* ----------------------------------------------------------------- users */
  Pages.users = async () => {
    const ps = state.paging.users;
    const params = new URLSearchParams({ page: ps.page, size: ps.size });
    if ($('#usr-search').value.trim()) params.set('q', $('#usr-search').value.trim());
    if ($('#usr-status').value) params.set('status_filter', $('#usr-status').value);

    const table = $('#usr-table');
    table.innerHTML = skeletonRows(5);
    const data = await api(`/users?${params}`);
    ps.total = data.total;

    if (!data.items.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState('users', t('common.empty'))}</td></tr></tbody>`;
      $('#usr-count').textContent = '';
      return;
    }

    table.innerHTML = `
      <thead><tr><th>${t('common.id')}</th><th>${t('common.username')}</th><th>${t('common.role')}</th>
      <th>${t('common.status')}</th><th>${t('users.telegram')}</th><th>${t('common.balance')}</th>
      <th>${t('users.configs')}</th><th>${t('common.usage')}</th><th>${t('users.joined')}</th>
      <th style="width:170px">${t('common.actions')}</th></tr></thead>
      <tbody>${data.items.map((u) => `
        <tr>
          <td class="mono">${num(u.id)}</td>
          <td><b>${esc(u.username)}</b><div class="sub">${esc(u.email || '')}</div></td>
          <td>${badge(u.role)}</td>
          <td>${badge(u.status)}</td>
          <td class="mono">${u.telegram_id ? esc(u.telegram_id) : '—'}</td>
          <td>${num(u.balance, { maximumFractionDigits: 2 })}</td>
          <td>${num(u.service_count)}</td>
          <td>${bytes(u.total_used_bytes)}</td>
          <td>${date(u.created_at)}</td>
          <td><div class="cell-actions">
            <button class="btn sm" data-uact="edit" data-id="${u.id}">${window.icon('pencil', { size: 14 })}</button>
            <button class="btn sm ${u.status === 'active' ? 'danger' : ''}" data-uact="${u.status === 'active' ? 'suspend' : 'activate'}" data-id="${u.id}">
              ${window.icon(u.status === 'active' ? 'power_off' : 'power', { size: 14 })}</button>
          </div></td>
        </tr>`).join('')}</tbody>`;

    $('#usr-count').textContent = `${num(data.total)} · ${t('common.page')} ${num(data.page)}/${num(data.pages)}`;
    window.hydrateIcons(table);

    $$('#usr-table [data-uact]').forEach((btn) => {
      btn.onclick = async () => {
        const id = Number(btn.dataset.id);
        const action = btn.dataset.uact;
        try {
          if (action === 'edit') return editUserDialog(data.items.find((x) => x.id === id));
          if (action === 'suspend' && !confirm(t('users.suspendConfirm'))) return;
          await api(`/users/${id}/${action}`, { method: 'POST' });
          toast(action === 'suspend' ? t('users.suspended') : t('users.activated'), 'ok');
          Pages.users();
        } catch (err) { toast(err.message, 'err'); }
      };
    });
  };

  function editUserDialog(u) {
    openModal(t('users.editTitle', { id: num(u.id), name: u.username }), `
      <div class="field-row">
        <div><label class="field">${t('common.email')}</label><input id="u-email" value="${esc(u.email || '')}"></div>
        <div><label class="field">${t('common.role')}</label><select id="u-role">
          ${['user', 'support', 'admin', 'owner'].map((r) => `<option value="${r}" ${r === u.role ? 'selected' : ''}>${t(`status.${r}`)}</option>`).join('')}
        </select></div>
        <div><label class="field">${t('common.status')}</label><select id="u-status">
          ${['active', 'disabled'].map((s) => `<option value="${s}" ${s === u.status ? 'selected' : ''}>${t(`status.${s}`)}</option>`).join('')}
        </select></div>
        <div><label class="field">${t('common.balance')}</label><input id="u-balance" type="number" step="0.01" value="${u.balance}"></div>
      </div>
      <label class="field">${t('users.newPassword')}</label><input id="u-pass" type="text">
      <label class="field">${t('common.note')}</label><textarea id="u-note" rows="2">${esc(u.note || '')}</textarea>`,
      [
        { label: t('common.cancel'), onClick: closeModal },
        {
          label: t('common.save'), kind: 'primary', icon: 'save',
          onClick: async (btn) => {
            btn.classList.add('loading');
            const body = {
              email: $('#u-email').value || null,
              role: $('#u-role').value,
              status: $('#u-status').value,
              balance: Number($('#u-balance').value),
              note: $('#u-note').value || null,
            };
            const pass = $('#u-pass').value.trim();
            if (pass) body.password = pass;
            try {
              await api(`/users/${u.id}`, { method: 'PATCH', body });
              closeModal(); toast(t('users.updated'), 'ok'); Pages.users();
            } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
          },
        },
      ]);
  }

  function userCreateDialog() {
    openModal(t('users.createTitle'), `
      <div class="field-row">
        <div><label class="field">${t('common.username')}</label><input id="nu-name"></div>
        <div><label class="field">${t('common.email')}</label><input id="nu-email"></div>
        <div><label class="field">${t('login.password')}</label><input id="nu-pass" type="text"></div>
        <div><label class="field">${t('common.role')}</label><select id="nu-role">
          <option value="user">${t('status.user')}</option><option value="support">${t('status.support')}</option><option value="admin">${t('status.admin')}</option>
        </select></div>
        <div><label class="field">${t('common.balance')}</label><input id="nu-bal" type="number" step="0.01" value="0"></div>
      </div>`,
      [
        { label: t('common.cancel'), onClick: closeModal },
        {
          label: t('common.create'), kind: 'primary', icon: 'plus',
          onClick: async (btn) => {
            btn.classList.add('loading');
            try {
              await api('/users', {
                method: 'POST',
                body: {
                  username: $('#nu-name').value,
                  email: $('#nu-email').value || null,
                  password: $('#nu-pass').value,
                  role: $('#nu-role').value,
                  balance: Number($('#nu-bal').value),
                },
              });
              closeModal(); toast(t('users.created'), 'ok'); Pages.users();
            } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
          },
        },
      ]);
  }

  /* ----------------------------------------------------------------- plans */
  Pages.plans = async () => {
    const data = await api('/plans?size=100&include_inactive=true');
    const grid = $('#plan-grid');
    if (!data.items.length) {
      grid.innerHTML = `<div class="card" style="grid-column:1/-1">${emptyState('tag', t('common.empty'))}</div>`;
      return;
    }
    grid.innerHTML = data.items.map((p, i) => `
      <div class="card hoverable" style="--i:${i}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:12px">
          <div>
            <h3 style="font-size:15px">${esc(p.name)}</h3>
            <div class="mono" style="color:var(--text-mute);font-size:11px">${esc(p.code)}</div>
          </div>
          <div style="text-align:end">
            <div style="font-size:19px;font-weight:700">${num(p.price, { maximumFractionDigits: 2 })} <span style="font-size:12px;color:var(--text-mute)">${esc(p.currency)}</span></div>
            ${p.is_active ? badge('active') : badge('disabled')}
          </div>
        </div>
        <div class="sep"></div>
        <dl class="kv">
          <dt>${t('plans.duration')}</dt><dd>${num(p.duration_days)} ${t('common.days')}</dd>
          <dt>${t('plans.traffic')}</dt><dd>${p.traffic_gb ? `${num(p.traffic_gb)} GB` : t('common.unlimited')}</dd>
          <dt>${t('plans.devices')}</dt><dd>${num(p.max_devices)}</dd>
          <dt>${t('plans.protocols')}</dt><dd><div class="chip-row">${(p.allowed_protocols || []).map((x) => `<span class="chip mono">${esc(x)}</span>`).join('') || `<span class="chip">${t('plans.any')}</span>`}</div></dd>
        </dl>
        ${isStaff() ? `<div style="display:flex;gap:8px;margin-block-start:14px">
          <button class="btn sm" data-pedit="${p.id}">${window.icon('pencil', { size: 14 })}<span>${t('common.edit')}</span></button>
          <button class="btn sm danger" data-pdel="${p.id}">${window.icon('trash', { size: 14 })}</button>
        </div>` : ''}
      </div>`).join('');
    window.hydrateIcons(grid);

    $$('[data-pdel]').forEach((b) => {
      b.onclick = async () => {
        if (!confirm(t('plans.deleteConfirm'))) return;
        try { await api(`/plans/${b.dataset.pdel}`, { method: 'DELETE' }); toast(t('plans.deleted'), 'ok'); Pages.plans(); }
        catch (err) { toast(err.message, 'err'); }
      };
    });
    $$('[data-pedit]').forEach((b) => {
      b.onclick = () => planDialog(data.items.find((x) => String(x.id) === b.dataset.pedit));
    });
  };

  function planDialog(plan) {
    const editing = Boolean(plan);
    openModal(editing ? t('plans.editTitle', { code: plan.code }) : t('plans.createTitle'), `
      <div class="field-row">
        <div><label class="field">${t('plans.code')}</label><input id="p-code" value="${esc(plan?.code || '')}" ${editing ? 'disabled' : ''}></div>
        <div><label class="field">${t('common.name')}</label><input id="p-name" value="${esc(plan?.name || '')}"></div>
        <div><label class="field">${t('plans.price')}</label><input id="p-price" type="number" step="0.01" value="${plan?.price ?? 5}"></div>
        <div><label class="field">${t('plans.currency')}</label><input id="p-cur" value="${esc(plan?.currency || 'USD')}"></div>
        <div><label class="field">${t('plans.duration')}</label><input id="p-days" type="number" value="${plan?.duration_days ?? 30}"></div>
        <div><label class="field">${t('plans.traffic')} GB</label><input id="p-gb" type="number" step="1" value="${plan?.traffic_gb ?? 50}"></div>
        <div><label class="field">${t('plans.devices')}</label><input id="p-dev" type="number" value="${plan?.max_devices ?? 2}"></div>
        <div><label class="field">${t('plans.nodeGroup')}</label><input id="p-group" value="${esc(plan?.node_group || '')}"></div>
      </div>
      <label class="field">${t('plans.protocols')}</label>
      <input id="p-proto" value="${esc((plan?.allowed_protocols || ['vless', 'vmess', 'trojan']).join(','))}">
      <label class="field">${t('plans.features')}</label>
      <input id="p-feat" value="${esc((plan?.features || []).join(','))}">
      <label class="switch" style="margin-block-start:14px">
        <input type="checkbox" id="p-active" ${plan?.is_active !== false ? 'checked' : ''}><span class="track"></span>
        <span>${t('common.enabled')}</span>
      </label>
      <label class="switch" style="margin-block-start:14px">
        <input type="checkbox" id="p-public" ${plan?.is_public !== false ? 'checked' : ''}><span class="track"></span>
        <span>${t('plans.public')}</span>
      </label>`,
      [
        { label: t('common.cancel'), onClick: closeModal },
        {
          label: editing ? t('common.save') : t('common.create'), kind: 'primary', icon: 'save',
          onClick: async (btn) => {
            const code = $('#p-code').value.trim();
            const name = $('#p-name').value.trim();
            if ((!editing && code.length < 2) || name.length < 2) {
              toast(t('plans.validation'), 'err');
              return;
            }
            btn.classList.add('loading');
            const body = {
              name,
              price: Number($('#p-price').value),
              currency: $('#p-cur').value,
              duration_days: Number($('#p-days').value),
              traffic_gb: Number($('#p-gb').value),
              max_devices: Number($('#p-dev').value),
              node_group: $('#p-group').value || null,
              allowed_protocols: $('#p-proto').value.split(',').map((s) => s.trim()).filter(Boolean),
              features: $('#p-feat').value.split(',').map((s) => s.trim()).filter(Boolean),
              is_active: $('#p-active').checked,
              is_public: $('#p-public').checked,
            };
            try {
              if (editing) await api(`/plans/${plan.id}`, { method: 'PATCH', body });
              else await api('/plans', { method: 'POST', body: { ...body, code } });
              closeModal(); toast(t('plans.saved'), 'ok'); Pages.plans();
            } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
          },
        },
      ]);
  }

  /* -------------------------------------------------------------- payments */
  Pages.payments = async () => {
    const params = new URLSearchParams({ page: 1, size: 50 });
    if ($('#pay-status').value) params.set('status_filter', $('#pay-status').value);
    const table = $('#pay-table');
    table.innerHTML = skeletonRows(5);
    const data = await api(`/payments?${params}`);

    if (!data.items.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState('card', t('common.empty'))}</td></tr></tbody>`;
      return;
    }
    const purposeKey = (p) => { const k = `payments.${p}`; return t(k) === k ? p : t(k); };
    table.innerHTML = `
      <thead><tr><th>${t('common.reference')}</th><th>${t('configs.user')}</th><th>${t('plans.title')}</th>
      <th>${t('payments.purpose')}</th><th>${t('common.amount')}</th><th>${t('common.method')}</th>
      <th>${t('common.status')}</th><th>${t('common.created')}</th><th style="width:200px">${t('common.actions')}</th></tr></thead>
      <tbody>${data.items.map((p) => `
        <tr>
          <td class="mono">${esc(p.reference)}</td>
          <td>${esc(p.username || p.user_id)}</td>
          <td>${esc(p.plan_name || '—')}</td>
          <td><span class="chip">${esc(purposeKey(p.purpose))}</span></td>
          <td><b>${num(p.amount, { maximumFractionDigits: 2 })} ${esc(p.currency)}</b></td>
          <td>${esc(p.method)}</td>
          <td>${badge(p.status)}</td>
          <td>${date(p.created_at, true)}</td>
          <td><div class="cell-actions">
            ${['pending', 'awaiting_review'].includes(p.status) && isStaff()
              ? `<button class="btn sm primary" data-pay="approve" data-id="${p.id}">${window.icon('check', { size: 14 })}</button>
                 <button class="btn sm danger" data-pay="reject" data-id="${p.id}">${window.icon('x', { size: 14 })}</button>` : ''}
            ${p.receipt_url ? `<a class="btn sm" href="${esc(p.receipt_url)}" target="_blank" rel="noopener">${window.icon('eye', { size: 14 })}</a>` : ''}
            ${p.status === 'paid' && isStaff() ? `<button class="btn sm" data-pay="refund" data-id="${p.id}">${window.icon('arrowLeft', { size: 14 })}</button>` : ''}
          </div></td>
        </tr>`).join('')}</tbody>`;
    window.hydrateIcons(table);

    $$('#pay-table [data-pay]').forEach((btn) => {
      btn.onclick = async () => {
        const action = btn.dataset.pay;
        if (action === 'reject' && !confirm(t('payments.rejectConfirm'))) return;
        try {
          await api(`/payments/${btn.dataset.id}/${action}`, { method: 'POST', body: { approve: action === 'approve' } });
          toast(action === 'approve' ? t('payments.approved') : action === 'reject' ? t('payments.rejected') : t('payments.refunded'), 'ok');
          Pages.payments();
          refreshBadges();
        } catch (err) { toast(err.message, 'err'); }
      };
    });
  };

  /* ----------------------------------------------------------------- nodes */
  Pages.nodes = async () => {
    const data = await api('/nodes?size=100');
    const grid = $('#node-grid');
    if (!data.items.length) {
      grid.innerHTML = `<div class="card" style="grid-column:1/-1">${emptyState('server', t('nodes.noNodes'), t('nodes.addFirst'))}</div>`;
      return;
    }
    grid.innerHTML = data.items.map((n, i) => `
      <div class="card hoverable" style="--i:${i}">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:10px">
          <div>
            <h3 style="font-size:15px">${esc(n.name)}</h3>
            <div class="mono" style="color:var(--text-mute);font-size:11px">${esc(n.public_host)} · ${esc(n.region || '—')}</div>
          </div>
          ${badge(n.status)}
        </div>
        <div class="sep"></div>
        <dl class="kv">
          <dt>${t('nodes.address')}</dt><dd class="mono" style="font-size:11.5px">${esc(n.address)}</dd>
          <dt>${t('nodes.xrayVersion')}</dt><dd>${esc(n.xray_version || t('common.unknown'))}</dd>
          <dt>${t('users.configs')}</dt><dd>${num(n.service_count)}${n.max_services ? ` / ${num(n.max_services)}` : ''}</dd>
          <dt>${t('nodes.heartbeat')}</dt><dd>${relative(n.last_heartbeat_at)}</dd>
        </dl>
        <div style="margin-block-start:12px;display:grid;gap:8px">
          ${[['CPU', n.cpu_percent], ['RAM', n.memory_percent], ['Disk', n.disk_percent]].map(([label, v]) => `
            <div style="display:flex;align-items:center;gap:10px">
              <span style="font-size:11px;color:var(--text-mute);width:34px">${label}</span>
              <span class="bar" style="flex:1"><i style="width:${Math.min(v || 0, 100)}%"></i></span>
              <span class="mono" style="font-size:11px;width:40px;text-align:end">${num(Math.round(v || 0))}%</span>
            </div>`).join('')}
        </div>
        ${n.last_error ? `<div class="sub" style="color:var(--danger);margin-block-start:10px;font-size:11.5px">${esc(n.last_error)}</div>` : ''}
        <div style="margin-block-start:14px;display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn sm" data-nact="health" data-id="${n.id}">${window.icon('activity', { size: 14 })}<span>${t('nodes.health')}</span></button>
          <button class="btn sm" data-nact="sync" data-id="${n.id}">${window.icon('refresh', { size: 14 })}<span>${t('nodes.sync')}</span></button>
          <button class="btn sm" data-nact="restart" data-id="${n.id}">${window.icon('power', { size: 14 })}</button>
          <button class="btn sm" data-nact="token" data-id="${n.id}">${window.icon('key', { size: 14 })}</button>
        </div>
      </div>`).join('');
    window.hydrateIcons(grid);

    $$('#node-grid [data-nact]').forEach((btn) => {
      btn.onclick = async () => {
        const id = btn.dataset.id;
        const act = btn.dataset.nact;
        try {
          if (act === 'health') {
            const h = await api(`/nodes/${id}/health`);
            openModal(t('nodes.healthTitle', { name: h.name }), `
              <dl class="kv">
                <dt>${t('common.status')}</dt><dd>${badge(h.status)} ${h.reachable ? `<span class="chip">${num(h.latency_ms || 0)} ms</span>` : ''}</dd>
                <dt>Xray</dt><dd>${h.xray_running ? badge('online') : badge('offline')} ${esc(h.xray_version || '')}</dd>
                <dt>CPU</dt><dd>${num(Math.round(h.cpu_percent || 0))}%</dd>
                <dt>RAM</dt><dd>${num(Math.round(h.memory_percent || 0))}%</dd>
                <dt>Disk</dt><dd>${num(Math.round(h.disk_percent || 0))}%</dd>
                <dt>${t('health.uptime')}</dt><dd>${relative(new Date(Date.now() - (h.uptime_seconds || 0) * 1000))}</dd>
                ${h.error ? `<dt>${t('common.unknown')}</dt><dd style="color:var(--danger)">${esc(h.error)}</dd>` : ''}
              </dl>`, [{ label: t('common.close'), onClick: closeModal }]);
          } else if (act === 'sync') {
            btn.classList.add('loading');
            const r = await api(`/nodes/${id}/sync`, { method: 'POST' });
            toast(r.ok ? t('nodes.synced', { n: num(r.applied) }) : t('nodes.syncFailed', { error: r.error || '' }), r.ok ? 'ok' : 'err');
            Pages.nodes();
          } else if (act === 'restart') {
            btn.classList.add('loading');
            await api(`/nodes/${id}/restart-xray`, { method: 'POST' });
            toast(t('nodes.restarted'), 'ok');
            Pages.nodes();
          } else {
            const r = await api(`/nodes/${id}/rotate-token`, { method: 'POST' });
            openModal(t('nodes.newToken'), `
              <p style="color:var(--text-dim);font-size:13px">${esc(t('nodes.newTokenHint'))}</p>
              <div class="copy-box">
                <input readonly id="ntok" value="${esc(r.api_token)}">
                <button class="btn sm" data-copy-target="ntok">${window.icon('copy', { size: 14 })}</button>
              </div>`, [{ label: t('common.close'), onClick: closeModal }]);
            bindCopyButtons($('#modal-body'));
          }
        } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
      };
    });
  };

  function nodeCreateDialog() {
    openModal(t('nodes.add'), `
      <div class="field-row">
        <div><label class="field">${t('common.name')}</label><input id="nn-name" placeholder="eu-1"></div>
        <div><label class="field">${t('nodes.region')}</label><input id="nn-region" placeholder="EU"></div>
        <div><label class="field">${t('nodes.address')}</label><input id="nn-address" placeholder="https://node1.example.com:8081"></div>
        <div><label class="field">${t('nodes.publicHost')}</label><input id="nn-host" placeholder="node1.example.com"></div>
        <div><label class="field">${t('nodes.apiToken')}</label><input id="nn-token" placeholder="nd_..."></div>
        <div><label class="field">${t('nodes.maxServices')}</label><input id="nn-max" type="number" value="0"></div>
      </div>
      <label class="field">${t('nodes.tags')}</label><input id="nn-tags" placeholder="eu,default">
      <div id="nn-prereq" class="hint" style="display:block;margin-block-start:14px">${esc(t('nodes.railwayHint'))}</div>`,
      [
        { label: t('common.cancel'), onClick: closeModal },
        {
          label: t('nodes.add'), kind: 'primary', icon: 'plus',
          onClick: async (btn) => {
            btn.classList.add('loading');
            try {
              await api('/nodes', {
                method: 'POST',
                body: {
                  name: $('#nn-name').value,
                  region: $('#nn-region').value || null,
                  address: $('#nn-address').value.trim(),
                  public_host: $('#nn-host').value,
                  api_token: $('#nn-token').value,
                  max_services: Number($('#nn-max').value),
                  tags: $('#nn-tags').value.split(',').map((s) => s.trim()).filter(Boolean),
                },
              });
              closeModal(); toast(t('nodes.added'), 'ok'); Pages.nodes();
            } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
          },
        },
      ]);
    api('/status').then((status) => {
      if (!status.nodes_total) return;
      const hint = $('#nn-prereq');
      if (hint) hint.textContent = t('nodes.railwayHint');
    }).catch(() => {});
  }

  function railwayNodeDialog() {
    openModal(t('nodes.railwaySetup'), `<p>${esc(t('nodes.railwayHint'))}</p>
      <p style="margin-block-start:12px;color:var(--text-mute)">${esc(t('nodes.railwayVariables'))}</p>`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('nodes.railwayRegister'), kind: 'primary', icon: 'server', onClick: async (btn) => {
        btn.classList.add('loading');
        try {
          await api('/nodes/railway', { method: 'POST' });
          closeModal(); toast(t('nodes.railwayRegistered'), 'ok'); Pages.nodes();
        } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
      } },
    ]);
  }

  /* -------------------------------------------------------------- inbounds */
  Pages.inbounds = async () => {
    const nodes = await api('/nodes?size=100');
    const sel = $('#inb-node');
    if (sel.options.length <= 1) {
      nodes.items.forEach((n) => sel.insertAdjacentHTML('beforeend', `<option value="${n.id}">${esc(n.name)}</option>`));
    }
    const nodeId = sel.value;
    const table = $('#inb-table');
    table.innerHTML = skeletonRows(4);
    const rows = await api(`/inbounds${nodeId ? `?node_id=${nodeId}` : ''}`);

    if (!rows.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState('plug', t('common.empty'))}</td></tr></tbody>`;
      return;
    }
    table.innerHTML = `
      <thead><tr><th>${t('inbounds.tag')}</th><th>${t('common.node')}</th><th>${t('common.protocol')}</th>
      <th>${t('inbounds.transport')}</th><th>${t('inbounds.security')}</th><th>${t('inbounds.port')}</th>
      <th>${t('inbounds.clients')}</th><th>${t('common.status')}</th><th style="width:150px">${t('common.actions')}</th></tr></thead>
      <tbody>${rows.map((i) => `
        <tr>
          <td><b>${esc(i.tag)}</b><div class="sub">${esc(i.remark || '')}</div></td>
          <td>${esc(nodes.items.find((n) => n.id === i.node_id)?.name || i.node_id)}</td>
          <td><span class="chip mono">${esc(i.protocol)}</span></td>
          <td>${esc(i.transport)}</td>
          <td>${esc(i.security)}</td>
          <td class="mono" title="Internal Xray port: ${num(i.port)}">${num(i.public_port || i.port)}${i.public_port && i.public_port !== i.port ? ` <small style="color:var(--text-mute)">(internal ${num(i.port)})</small>` : ''}</td>
          <td>${num(i.service_count)}</td>
          <td>${i.is_active ? badge('active') : badge('disabled')}</td>
          <td><div class="cell-actions">
            <button class="btn sm" data-iact="validate" data-id="${i.id}">${window.icon('shield', { size: 14 })}</button>
            <button class="btn sm" data-iact="sync" data-id="${i.id}">${window.icon('refresh', { size: 14 })}</button>
          </div></td>
        </tr>`).join('')}</tbody>`;
    window.hydrateIcons(table);

    $$('#inb-table [data-iact]').forEach((btn) => {
      btn.onclick = async () => {
        try {
          if (btn.dataset.iact === 'validate') {
            const r = await api(`/inbounds/${btn.dataset.id}/validate`);
            openModal(t('inbounds.validationTitle'), `
              <p>${r.ok ? badge('ok') : badge('failed')}</p>
              ${r.errors.length ? `<h4 style="margin-block:12px 6px;font-size:13px;color:var(--danger)">${t('inbounds.errors')}</h4>
                <ul style="margin:0;padding-inline-start:18px;font-size:13px">${r.errors.map((e) => `<li>${esc(e)}</li>`).join('')}</ul>` : ''}
              ${r.warnings.length ? `<h4 style="margin-block:12px 6px;font-size:13px;color:var(--warn)">${t('inbounds.warnings')}</h4>
                <ul style="margin:0;padding-inline-start:18px;font-size:13px">${r.warnings.map((w) => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}`,
              [{ label: t('common.close'), onClick: closeModal }]);
          } else {
            const r = await api(`/inbounds/${btn.dataset.id}/sync`, { method: 'POST' });
            toast(r.detail, 'ok');
          }
        } catch (err) { toast(err.message, 'err'); }
      };
    });
  };

  function inboundCreateDialog() {
    openModal(t('inbounds.createTitle'), `
      <div class="field-row">
        <div><label class="field">${t('common.node')}</label><select id="ni-node"><option value="">${t('common.loading')}</option></select></div>
        <div><label class="field">${t('inbounds.tag')}</label><input id="ni-tag" placeholder="vless-reality"></div>
        <div><label class="field">${t('common.protocol')}</label><select id="ni-proto">
          <option>vless</option><option>vmess</option><option>trojan</option><option>shadowsocks</option></select></div>
        <div><label class="field">${t('inbounds.port')}</label><input id="ni-port" type="number" value="443"></div>
        <div><label class="field">${t('inbounds.transport')}</label><select id="ni-net">
          <option>tcp</option><option>ws</option><option>grpc</option><option>httpupgrade</option><option>xhttp</option></select></div>
        <div><label class="field">${t('inbounds.security')}</label><select id="ni-sec">
          <option>none</option><option>tls</option><option>reality</option></select></div>
        <div><label class="field">SNI</label><input id="ni-sni"></div>
        <div><label class="field">Path / serviceName</label><input id="ni-path"></div>
        <div><label class="field">Flow</label><input id="ni-flow" placeholder="xtls-rprx-vision"></div>
        <div><label class="field">Reality public key</label><input id="ni-pbk"></div>
        <div><label class="field">Reality private key</label><input id="ni-pvk"></div>
        <div><label class="field">Reality short id</label><input id="ni-sid"></div>
      </div>
      <p id="ni-prereq" style="color:var(--text-mute);font-size:12px;margin-block-start:12px">${esc(t('inbounds.realityKeysHint'))}</p>`,
      [
        { label: t('common.cancel'), onClick: closeModal },
        {
          label: t('common.create'), kind: 'primary', icon: 'plus',
          onClick: async (btn) => {
            if (!$('#ni-node').value) { toast(t('inbounds.noNodes'), 'err'); return; }
            btn.classList.add('loading');
            const net = $('#ni-net').value;
            const body = {
              node_id: Number($('#ni-node').value),
              tag: $('#ni-tag').value,
              protocol: $('#ni-proto').value,
              port: Number($('#ni-port').value),
              transport: net,
              security: $('#ni-sec').value,
              sni: $('#ni-sni').value || null,
              flow: $('#ni-flow').value || null,
              reality_public_key: $('#ni-pbk').value || null,
              reality_private_key: $('#ni-pvk').value || null,
              reality_short_ids: $('#ni-sid').value ? [$('#ni-sid').value] : [],
            };
            if (net === 'grpc') body.service_name = $('#ni-path').value || 'grpc';
            else body.path = $('#ni-path').value || '/';
            try {
              await api('/inbounds', { method: 'POST', body });
              closeModal(); toast(t('inbounds.created'), 'ok'); Pages.inbounds();
            } catch (err) { toast(err.message, 'err'); btn.classList.remove('loading'); }
          },
        },
      ]);
    api('/nodes?size=100').then((nodes) => {
      const select = $('#ni-node');
      if (!select) return;
      select.innerHTML = `<option value="">${esc(t('common.select'))}</option>`;
      nodes.items.forEach((n) => select.insertAdjacentHTML('beforeend', `<option value="${n.id}">${esc(n.name)} · #${n.id}</option>`));
      if (!nodes.items.length) {
        const hint = $('#ni-prereq');
        hint.innerHTML = `${esc(t('inbounds.noNodes'))} <button type="button" class="btn sm" id="ni-add-node">${esc(t('nodes.add'))}</button>`;
        $('#ni-add-node').onclick = () => { closeModal(); nodeCreateDialog(); };
      }
    }).catch((err) => { const hint = $('#ni-prereq'); if (hint) hint.textContent = err.message; });
  }

  /* --------------------------------------------------------------- reports */
  Pages.reports = async ({ silent } = {}) => {
    const [traffic, revenue, summary] = await Promise.all([
      api('/reports/traffic?days=30'),
      isStaff() ? api('/reports/revenue?days=30').catch(() => []) : Promise.resolve([]),
      isStaff() ? api('/reports/summary').catch(() => null) : Promise.resolve(null),
    ]);
    drawLineChart($('#chart-report'), traffic, 'total_bytes', { animate: !silent });

    if (revenue.length) {
      $('#revenue-list').innerHTML = '<canvas class="chart" id="chart-revenue"></canvas>';
      drawBarChart($('#chart-revenue'), revenue, { animate: !silent });
    } else {
      $('#revenue-list').innerHTML = emptyState('wallet', t('reports.noPayments'));
    }

    const statusChip = (k, v) => {
      const key = `status.${k}`;
      return `<span class="chip">${esc(t(key) === key ? k : t(key))}: <b>${num(v)}</b></span>`;
    };
    $('#summary-box').innerHTML = summary
      ? `<dl class="kv">
          <dt>${t('reports.byStatus')}</dt><dd><div class="chip-row">${Object.entries(summary.services_by_status).map(([k, v]) => statusChip(k, v)).join('') || '—'}</div></dd>
          <dt>${t('reports.byProtocol')}</dt><dd><div class="chip-row">${Object.entries(summary.services_by_protocol).map(([k, v]) => `<span class="chip mono">${esc(k)}: <b>${num(v)}</b></span>`).join('') || '—'}</div></dd>
          <dt>${t('reports.byNode')}</dt><dd><div class="chip-row">${(summary.services_by_node || []).map((n) => `<span class="chip">${esc(n.node)}: <b>${num(n.services)}</b></span>`).join('') || '—'}</div></dd>
          <dt>${t('reports.totalTraffic')}</dt><dd>${bytes(summary.total_traffic_bytes)}</dd>
          <dt>${t('reports.revenueMonth')}</dt><dd>${num(summary.revenue_this_month, { maximumFractionDigits: 2 })}</dd>
        </dl>`
      : emptyState('chartPie', t('common.empty'));
  };

  /* ---------------------------------------------------------------- health */
  Pages.health = async () => {
    const report = await api('/health');
    const tone = report.status === 'ok' ? 'ok' : report.status === 'degraded' ? 'warn' : 'danger';
    $('#health-cards').innerHTML = `
      <div class="card">
        <div class="card-head"><i data-icon="activity"></i><h3>${t('health.overall')}</h3></div>
        <div style="font-size:24px;font-weight:700">${badge(report.status)}</div>
        <div class="sep"></div>
        <dl class="kv">
          <dt>${t('health.version')}</dt><dd>${esc(report.version)}</dd>
          <dt>${t('health.environment')}</dt><dd>${esc(report.environment)}</dd>
          <dt>${t('health.uptime')}</dt><dd>${relative(new Date(Date.now() - report.uptime_seconds * 1000))}</dd>
          <dt>${t('dashboard.nodes')}</dt><dd>${num(report.nodes_online)}/${num(report.nodes_total)}</dd>
        </dl>
      </div>
      <div class="card">
        <div class="card-head"><i data-icon="cpu"></i><h3>${t('health.components')}</h3></div>
        ${report.components.map((c) => {
          const key = `health.component.${c.name}`;
          return `<div class="list-row">
            ${badge(c.status)}
            <div class="grow"><div class="title">${esc(t(key) === key ? c.name : t(key))}</div>
            <div class="meta mono">${esc(c.detail || '')}${c.latency_ms != null ? ` · ${num(c.latency_ms)} ms` : ''}</div></div>
          </div>`;
        }).join('')}
      </div>`;
    window.hydrateIcons($('#health-cards'));

    const dot = $('#health-dot');
    if (dot) dot.style.background = `var(--${tone === 'ok' ? 'ok' : tone === 'warn' ? 'warn' : 'danger'})`;
    const label = $('#health-label');
    if (label) label.textContent = `${num(report.nodes_online)}/${num(report.nodes_total)}`;
  };

  /* ---------------------------------------------------------------- alerts */
  Pages.alerts = async () => {
    const activeOnly = $('#alerts-active-only').checked;
    const table = $('#alerts-table');
    table.innerHTML = skeletonRows(4);
    const rows = await api(`/alerts?active_only=${activeOnly}&limit=200`);
    if (!rows.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState('check', t('alerts.noAlerts'))}</td></tr></tbody>`;
      return;
    }
    table.innerHTML = `
      <thead><tr><th>${t('alerts.level')}</th><th>${t('alerts.code')}</th><th>${t('common.name')}</th>
      <th>${t('alerts.entity')}</th><th>${t('alerts.count')}</th><th>${t('common.created')}</th><th></th></tr></thead>
      <tbody>${rows.map((a) => `
        <tr>
          <td>${badge(a.level)}</td>
          <td class="mono">${esc(a.code)}</td>
          <td><b>${esc(a.title)}</b><div class="sub">${esc(a.message)}</div></td>
          <td class="mono">${esc(a.entity_type || '')}${a.entity_id ? `#${a.entity_id}` : ''}</td>
          <td>${num(a.occurrences)}</td>
          <td>${date(a.created_at, true)}</td>
          <td>${a.is_active ? `<button class="btn sm" data-ack="${a.id}">${window.icon('check', { size: 14 })}<span>${t('alerts.ack')}</span></button>` : badge('ok')}</td>
        </tr>`).join('')}</tbody>`;
    window.hydrateIcons(table);

    $$('#alerts-table [data-ack]').forEach((b) => {
      b.onclick = async () => {
        try { await api(`/alerts/${b.dataset.ack}/ack`, { method: 'POST' }); toast(t('alerts.acked'), 'ok'); Pages.alerts(); refreshBadges(); }
        catch (err) { toast(err.message, 'err'); }
      };
    });
  };

  /* ------------------------------------------------------------------ logs */
  Pages.logs = async () => {
    const action = $('#log-action').value.trim();
    const table = $('#log-table');
    table.innerHTML = skeletonRows(6);
    const data = await api(`/logs/audit?size=100${action ? `&action=${encodeURIComponent(action)}` : ''}`);
    if (!data.items.length) {
      table.innerHTML = `<tbody><tr><td>${emptyState('scroll', t('logs.noLogs'))}</td></tr></tbody>`;
      return;
    }
    table.innerHTML = `
      <thead><tr><th>${t('logs.time')}</th><th>${t('logs.actor')}</th><th>${t('logs.action')}</th>
      <th>${t('alerts.entity')}</th><th>${t('common.status')}</th><th>${t('logs.ip')}</th><th>${t('logs.meta')}</th></tr></thead>
      <tbody>${data.items.map((l) => `
        <tr>
          <td class="mono">${date(l.created_at, true)}</td>
          <td>${esc(l.actor_label || l.actor_id || l.actor_type)}</td>
          <td><span class="chip mono">${esc(l.action)}</span></td>
          <td class="mono">${esc(l.entity_type || '')}${l.entity_id ? `#${l.entity_id}` : ''}</td>
          <td>${badge(l.status)}</td>
          <td class="mono">${esc(l.ip_address || '—')}</td>
          <td class="mono" style="font-size:11px;color:var(--text-mute);max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(JSON.stringify(l.meta))}</td>
        </tr>`).join('')}</tbody>`;
  };

  /* -------------------------------------------------------------- telegram */
  function setHint(selector, text, tone = 'muted') {
    const el = $(selector);
    if (!el) return;
    el.className = `tg-hint ${tone}`;
    if (!text) {
      el.innerHTML = '';
      return;
    }
    // Each branch calls icon() with a literal name so the UI contract test can
    // verify every glyph actually exists in icons.js.
    let glyph;
    if (tone === 'ok') glyph = window.icon('check', { size: 13 });
    else if (tone === 'err') glyph = window.icon('warning', { size: 13 });
    else if (tone === 'warn') glyph = window.icon('warning', { size: 13 });
    else glyph = window.icon('info', { size: 13 });
    el.innerHTML = `${glyph}<span>${esc(text)}</span>`;
  }

  function parseIdList(raw) {
    return String(raw || '')
      .split(/[,\s;]+/)
      .map((piece) => piece.trim())
      .filter(Boolean)
      .map((piece) => Number(piece))
      .filter((value) => Number.isFinite(value) && value > 0);
  }

  Pages.telegram = async () => {
    const [cfg, status] = await Promise.all([api('/telegram/config'), api('/telegram/status')]);

    // ---- form ----------------------------------------------------------
    $('#tg-token').value = '';
    $('#tg-token').placeholder = cfg.has_token ? cfg.token_hint : t('telegram.tokenPlaceholder');
    $('#tg-username').value = cfg.bot_username || '';
    $('#tg-secret').value = '';
    $('#tg-secret').placeholder = cfg.webhook_secret_hint || t('telegram.webhookSecretPlaceholder');
    $('#tg-admins').value = (cfg.admin_ids || []).join(', ');
    $('#tg-enabled').checked = cfg.enabled;
    $('#tg-auto').checked = cfg.auto_set_webhook;

    const sourceLabel = cfg.token_source === 'database' ? t('telegram.tokenFromDb') : t('telegram.tokenFromEnv');
    setHint(
      '#tg-token-state',
      `${cfg.has_token ? t('telegram.tokenSet', { hint: cfg.token_hint }) : t('telegram.tokenMissing')} · ${sourceLabel}`,
      cfg.has_token ? 'ok' : 'warn',
    );
    setHint(
      '#tg-admins-state',
      cfg.admin_ids.length
        ? t('telegram.adminsSet', { n: num(cfg.admin_ids.length) })
        : t('telegram.adminsMissing'),
      cfg.admin_ids.length ? 'ok' : 'warn',
    );

    const badge = $('#tg-source-badge');
    badge.hidden = false;
    badge.textContent = sourceLabel;
    badge.className = `badge no-dot ${cfg.token_source === 'database' ? 'active' : 'info'}`;

    // ---- status --------------------------------------------------------
    const webhookUrl = status.webhook?.url || '';
    const webhookOk = Boolean(webhookUrl) && webhookUrl === status.expected_webhook;
    $('#tg-status').innerHTML = `
      <dt>${t('telegram.configured')}</dt><dd>${cfg.has_token ? badge('ok') : badge('failed')}</dd>
      <dt>${t('common.status')}</dt><dd>${status.enabled ? badge('ok') : badge('disabled')}</dd>
      <dt>${t('telegram.bot')}</dt><dd>${esc(status.bot?.username ? '@' + status.bot.username : '—')}</dd>
      <dt>${t('telegram.webhook')}</dt><dd>${webhookOk ? badge('ok') : webhookUrl ? badge('warning') : badge('disabled')}</dd>
      <dt>${t('telegram.pending')}</dt><dd>${num(status.webhook?.pending_update_count ?? 0)}</dd>
      <dt>${t('telegram.linked')}</dt><dd>${num(status.linked_accounts)}</dd>`;
    $('#tg-url').textContent = status.expected_webhook;

    // ---- how-to --------------------------------------------------------
    // The step strings intentionally contain <b>/<code> markup from our own
    // dictionary, so they are injected as HTML rather than escaped text.
    $('#tg-steps').innerHTML = [1, 2, 3, 4]
      .map((i) => `<li>${t(`telegram.step${i}`)}</li>`)
      .join('');
  };

  async function saveTelegramConfig() {
    const payload = {
      bot_username: $('#tg-username').value.trim(),
      admin_ids: parseIdList($('#tg-admins').value),
      enabled: $('#tg-enabled').checked,
      auto_set_webhook: $('#tg-auto').checked,
    };
    const token = $('#tg-token').value.trim();
    if (token) payload.bot_token = token;
    const secret = $('#tg-secret').value.trim();
    if (secret) payload.webhook_secret = secret;

    await api('/telegram/config', { method: 'PUT', body: payload });
    toast(t('telegram.saved'), 'ok');
    await Pages.telegram();
    refreshBadges();
  }

  async function validateTelegramToken() {
    const typed = $('#tg-token').value.trim();
    const query = typed ? `?token=${encodeURIComponent(typed)}` : '';
    const result = await api(`/telegram/validate${query}`, { method: 'POST' });
    if (result.ok) {
      setHint('#tg-token-state', t('telegram.valid', { username: result.bot?.username || '' }), 'ok');
      if (!typed) await Pages.telegram();
    } else {
      setHint('#tg-token-state', result.error || t('telegram.invalid'), 'err');
    }
  }

  /* -------------------------------------------------------------- settings */
  Pages.settings = async () => {
    const [settings, backups, info] = await Promise.all([
      api('/settings'),
      api('/backups'),
      api('/system/info'),
    ]);

    $('#settings-form').innerHTML = settings.length
      ? settings.map((s) => `
        <label class="field">${esc(s.key)} <span class="hint">${esc(s.description || '')}</span></label>
        <div class="copy-box" style="margin-block-end:6px">
          <input data-key="${esc(s.key)}" value="${esc(typeof s.value === 'string' ? s.value : JSON.stringify(s.value))}">
          <button class="btn sm" data-save="${esc(s.key)}">${window.icon('save', { size: 14 })}</button>
        </div>`).join('')
      : emptyState('sliders', t('common.empty'), t('settings.runtimeHint'));

    $$('[data-save]').forEach((b) => {
      b.onclick = async () => {
        const input = $(`input[data-key="${CSS.escape(b.dataset.save)}"]`);
        let value = input.value;
        try { value = JSON.parse(value); } catch { /* keep as string */ }
        try { await api(`/settings/${encodeURIComponent(b.dataset.save)}`, { method: 'PUT', body: { value } }); toast(t('common.saved'), 'ok'); }
        catch (err) { toast(err.message, 'err'); }
      };
    });

    $('#backup-list').innerHTML = backups.items.length
      ? `<div class="table-scroll"><table><tbody>${backups.items.map((b) => `<tr>
          <td class="mono" style="font-size:11.5px">${esc(b.filename)}</td>
          <td>${bytes(b.size_bytes)}</td>
          <td>${badge(b.status)}</td>
          <td class="mono" style="font-size:11px">${date(b.created_at, true)}</td></tr>`).join('')}</tbody></table></div>
         <div class="sub" style="margin-block-start:10px;color:var(--text-mute);font-size:12px">${t('settings.diskUsage')}: ${bytes(backups.disk.bytes)} · ${num(backups.disk.files)} ${t('settings.filename')}</div>`
      : emptyState('database', t('settings.noBackups'));

    $('#system-info').innerHTML = `
      <dt>${t('settings.app')}</dt><dd>${esc(info.settings.app_name)} v${esc(info.settings.version)}</dd>
      <dt>${t('health.environment')}</dt><dd>${esc(info.settings.environment)}</dd>
      <dt>Panel URL</dt><dd class="mono" style="font-size:11.5px">${esc(info.settings.panel_base_url)}</dd>
      <dt>${t('settings.database')}</dt><dd class="mono">${esc(info.database.driver)}</dd>
      <dt>${t('settings.backupDir')}</dt><dd class="mono" style="font-size:11.5px">${esc(info.paths.backup_dir)}</dd>
      <dt>${t('dashboard.users')}</dt><dd>${num(info.counts.users)}</dd>`;
  };

  /* =================================================================== router */
  const PAGES = {
    dashboard: { icon: 'dashboard', title: 'nav.dashboard', sub: 'dashboard.subtitle' },
    configs: { icon: 'layers', title: 'nav.configs' },
    users: { icon: 'users', title: 'nav.users', staff: true },
    plans: { icon: 'tag', title: 'nav.plans' },
    payments: { icon: 'card', title: 'nav.payments', staff: true },
    nodes: { icon: 'server', title: 'nav.nodes', staff: true },
    inbounds: { icon: 'plug', title: 'nav.inbounds', staff: true },
    reports: { icon: 'chart', title: 'nav.reports' },
    health: { icon: 'activity', title: 'nav.health', staff: true },
    alerts: { icon: 'bell', title: 'nav.alerts', staff: true },
    logs: { icon: 'scroll', title: 'nav.logs', staff: true },
    settings: { icon: 'sliders', title: 'nav.settings', staff: true },
    telegram: { icon: 'send', title: 'nav.telegram', staff: true },
  };

  async function navigate(page, { push = true, silent = false } = {}) {
    let meta = PAGES[page] || PAGES.dashboard;
    if (meta.staff && !isStaff()) { page = 'dashboard'; meta = PAGES.dashboard; }
    state.page = page;
    if (location.pathname === '/login') return;
    if (push && location.hash !== `#/${page}`) history.replaceState(null, '', `${location.pathname === '/' ? '/panel' : location.pathname}#/${page}`);

    $$('#nav .nav-item').forEach((el) => el.classList.toggle('active', el.dataset.page === page));
    $('#page-title').textContent = t(meta.title);
    $('#page-sub').textContent = meta.sub ? t(meta.sub) : '';
    $$('.page').forEach((el) => el.classList.toggle('active', el.id === `page-${page}`));
    $('#sidebar').classList.remove('open');
    $('#scrim').classList.remove('open');

    const active = $(`#page-${page}`);
    if (active) {
      active.classList.remove('anim-up');
      void active.offsetWidth;
      active.classList.add('anim-up');
    }

    try {
      await Pages[page]?.({ silent });
    } catch (err) {
      toast(err.message || t('error.generic'), 'err');
    }
  }

  /* ========================================================= command palette */
  function paletteOpen() {
    $('#palette-backdrop').classList.add('open');
    $('#palette-input').value = '';
    paletteRender('');
    setTimeout(() => $('#palette-input').focus(), 30);
  }
  const paletteClose = () => $('#palette-backdrop').classList.remove('open');

  function paletteRender(query) {
    const q = query.trim().toLowerCase();
    const items = [];
    Object.entries(PAGES).forEach(([key, meta]) => {
      if (meta.staff && !isStaff()) return;
      const label = t(meta.title);
      if (!q || label.toLowerCase().includes(q) || key.includes(q)) {
        items.push({ kind: 'page', key, label, icon: meta.icon });
      }
    });
    if (isStaff() && (!q || 'new config'.includes(q) || 'کانفیگ جدید'.includes(q))) {
      items.push({ kind: 'action', action: 'new-config', label: t('configs.new'), icon: 'plus' });
    }
    if (!q || 'theme'.includes(q) || 'پوسته'.includes(q)) {
      items.push({ kind: 'action', action: 'toggle-theme', label: t('theme.switch'), icon: Theme.get() === 'dark' ? 'sun' : 'moon' });
    }
    if (!q || 'language'.includes(q) || 'زبان'.includes(q)) {
      items.push({ kind: 'action', action: 'toggle-lang', label: t('lang.switch'), icon: 'globe' });
    }

    state.paletteItems = items;
    state.paletteIndex = 0;
    $('#palette-results').innerHTML = items.length
      ? items.map((item, i) => `
        <div class="item ${i === 0 ? 'active' : ''}" data-index="${i}">
          <span class="ico">${window.icon(item.icon, { size: 17 })}</span>
          <span class="grow">${esc(item.label)}</span>
          ${item.kind === 'page' ? `<kbd>${esc(item.key)}</kbd>` : ''}
        </div>`).join('')
      : emptyState('search', t('common.noResults'));

    $$('#palette-results .item').forEach((el) => {
      el.onclick = () => paletteRun(Number(el.dataset.index));
    });
  }

  function paletteRun(index) {
    const item = state.paletteItems[index];
    if (!item) return;
    paletteClose();
    if (item.kind === 'page') return navigate(item.key);
    if (item.action === 'new-config') return createServiceDialog();
    if (item.action === 'toggle-theme') return Theme.toggle();
    if (item.action === 'toggle-lang') return window.I18N.toggle();
  }

  function paletteMove(delta) {
    if (!state.paletteItems.length) return;
    state.paletteIndex = (state.paletteIndex + delta + state.paletteItems.length) % state.paletteItems.length;
    $$('#palette-results .item').forEach((el, i) => el.classList.toggle('active', i === state.paletteIndex));
    $$('#palette-results .item')[state.paletteIndex]?.scrollIntoView({ block: 'nearest' });
  }

  function profileDialog() {
    const currentName = esc(state.me?.username || '');
    openModal(t('profile.title'), `
      <label class="field" for="profile-username">${t('profile.username')}</label>
      <input id="profile-username" minlength="3" maxlength="64" value="${currentName}" autocomplete="username" />
      <label class="field" for="profile-current">${t('profile.currentPassword')}</label>
      <input id="profile-current" type="password" autocomplete="current-password" />
      <label class="field" for="profile-new">${t('profile.newPassword')}</label>
      <input id="profile-new" type="password" minlength="8" autocomplete="new-password" />
      <label class="field" for="profile-confirm">${t('profile.confirmPassword')}</label>
      <input id="profile-confirm" type="password" minlength="8" autocomplete="new-password" />`, [
      { label: t('common.cancel'), onClick: closeModal },
      { label: t('common.save'), kind: 'primary', onClick: async (btn) => {
        const username = $('#profile-username').value.trim();
        const current_password = $('#profile-current').value;
        const new_password = $('#profile-new').value;
        const confirmation = $('#profile-confirm').value;
        if (!current_password) return toast(t('profile.currentRequired'), 'warn');
        if (new_password && new_password !== confirmation) return toast(t('profile.passwordMismatch'), 'warn');
        if (!new_password && username === state.me.username) return toast(t('profile.noChanges'), 'warn');
        btn.classList.add('loading');
        try {
          const updated = await api('/auth/profile', { method: 'PATCH', body: {
            username, current_password, ...(new_password ? { new_password } : {}),
          } });
          state.me = updated;
          $('#me-name').textContent = updated.username;
          $('#me-avatar').textContent = updated.username.slice(0, 1).toUpperCase();
          closeModal();
          toast(t('profile.updated'), 'ok');
          if (new_password) {
            try {
              const pair = await api('/auth/login', { method: 'POST', body: { username, password: new_password } });
              setTokens(pair.access_token, pair.refresh_token);
            } catch {
              logout(t('login.sessionExpired'));
            }
          }
        } catch (err) { toast(err.message, 'err'); }
        finally { btn.classList.remove('loading'); }
      } },
    ]);
  }

  /* ================================================================= session */
  function logout(message) {
    state.token = ''; state.refresh = ''; state.me = null;
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    document.documentElement.classList.add('login-route');
    if (location.pathname !== '/login') history.replaceState(null, '', '/login');
    $('#app').hidden = true;
    $('#login').hidden = false;
    $('#login-msg').textContent = message || '';
  }

  async function enterApp() {
    document.documentElement.classList.remove('login-route');
    if (location.pathname === '/' || location.pathname === '/login') {
      history.replaceState(null, '', `/panel${location.hash}`);
    }
    $('#login').hidden = true;
    $('#app').hidden = false;
    $('#me-name').textContent = state.me.username;
    $('#me-role').textContent = t(`status.${state.me.role}`);
    $('#me-avatar').textContent = state.me.username.slice(0, 1).toUpperCase();
    if (location.hash && !PAGES[location.hash.replace('#/', '')]) {
      history.replaceState(null, '', location.pathname);
    }

    $$('[data-staff]').forEach((el) => { el.hidden = !isStaff(); });

    const hash = (location.hash || '').replace('#/', '');
    await navigate(PAGES[hash] ? hash : 'dashboard', { push: false });
    refreshBadges();
  }

  async function refreshBadges() {
    try {
      const status = await api('/status');
      $('#health-label').textContent = `${num(status.nodes_online)}/${num(status.nodes_total)}`;
      $('#env-badge').textContent = status.environment;
      const dot = $('#health-dot');
      if (dot) dot.style.background = `var(--${status.status === 'operational' ? 'ok' : 'warn'})`;
    } catch { /* cosmetic */ }

    if (!isStaff()) return;
    try {
      const pending = await api('/payments/pending?limit=100');
      const el = $('#nav-payments-count');
      el.hidden = !pending.length;
      el.textContent = num(pending.length);
    } catch { /* ignore */ }
    try {
      const alerts = await api('/alerts?active_only=true&limit=100');
      const el = $('#nav-alerts-count');
      el.hidden = !alerts.length;
      el.textContent = num(alerts.length);
    } catch { /* ignore */ }
  }

  /* ================================================================== wiring */
  function wire() {
    window.hydrateIcons(document);
    applyStaticI18n();

    $('#btn-theme').onclick = () => Theme.toggle();
    $('#login-theme').onclick = () => Theme.toggle();
    $('#btn-lang').onclick = () => window.I18N.toggle();
    $('#login-lang').onclick = () => window.I18N.toggle();

    window.I18N.onChange(() => {
      applyStaticI18n();
      if (state.me) {
        $('#me-role').textContent = t(`status.${state.me.role}`);
        navigate(state.page, { push: false });
        refreshBadges();
      } else {
        loadPublicStatus();
      }
    });

    $('#login-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const btn = $('#login-btn');
      const msg = $('#login-msg');
      msg.textContent = '';
      if (!$('#username').value.trim() || !$('#password').value) {
        msg.textContent = t('login.needCredentials');
        return;
      }
      btn.classList.add('loading');
      try {
        const data = await api('/auth/login', {
          method: 'POST',
          body: {
            username: $('#username').value.trim(),
            password: $('#password').value,
            totp_code: $('#totp').value.trim() || null,
          },
        });
        setTokens(data.access_token, data.refresh_token);
        state.me = await api('/auth/me');
        $('#password').value = '';
        await enterApp();
      } catch (err) {
        msg.textContent = err.status === 429
          ? t('login.throttled')
          : err.status === 423 ? t('login.locked') : (err.message || t('login.failed'));
      } finally {
        btn.classList.remove('loading');
      }
    });

    $('#btn-logout').onclick = async () => {
      try { await api('/auth/logout', { method: 'POST' }); } catch { /* ignore */ }
      logout();
    };
    $('#btn-profile').onclick = profileDialog;
    $('#btn-refresh').onclick = () => { navigate(state.page); refreshBadges(); };

    $('#btn-menu').onclick = () => {
      $('#sidebar').classList.toggle('open');
      $('#scrim').classList.toggle('open');
    };
    $('#scrim').onclick = () => {
      $('#sidebar').classList.remove('open');
      $('#scrim').classList.remove('open');
    };

    $$('#nav .nav-item').forEach((el) => { el.onclick = () => navigate(el.dataset.page); });
    window.addEventListener('hashchange', () => {
      const page = (location.hash || '').replace('#/', '');
      if (PAGES[page] && page !== state.page) navigate(page, { push: false });
    });
    window.addEventListener('popstate', () => {
      if (location.pathname === '/login') {
        document.documentElement.classList.add('login-route');
        $('#app').hidden = true;
        $('#login').hidden = false;
      } else if (state.token && !state.me) {
        restoreSession();
      } else if (state.me) {
        document.documentElement.classList.remove('login-route');
        $('#login').hidden = true;
        $('#app').hidden = false;
        const page = (location.hash || '').replace('#/', '');
        if (PAGES[page]) navigate(page, { push: false });
      }
    });

    $('#modal-close').onclick = closeModal;
    $('#modal').onclick = (e) => { if (e.target.id === 'modal') closeModal(); };

    $('#btn-palette').onclick = paletteOpen;
    $('#palette-backdrop').onclick = (e) => { if (e.target.id === 'palette-backdrop') paletteClose(); };
    $('#palette-input').oninput = (e) => paletteRender(e.target.value);
    $('#palette-input').onkeydown = (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); paletteMove(1); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); paletteMove(-1); }
      else if (e.key === 'Enter') { e.preventDefault(); paletteRun(state.paletteIndex); }
      else if (e.key === 'Escape') paletteClose();
    };

    document.addEventListener('keydown', (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if ($('#palette-backdrop').classList.contains('open')) paletteClose(); else paletteOpen();
      } else if (e.key === 'Escape') {
        closeModal();
        paletteClose();
      }
    });

    // configs
    $('#svc-search').oninput = debounce(() => { state.paging.configs.page = 1; Pages.configs(); }, 350);
    $('#svc-status').onchange = () => { state.paging.configs.page = 1; Pages.configs(); };
    $('#svc-prev').onclick = () => { if (state.paging.configs.page > 1) { state.paging.configs.page -= 1; Pages.configs(); } };
    $('#svc-next').onclick = () => {
      const ps = state.paging.configs;
      if (ps.page * ps.size < ps.total) { ps.page += 1; Pages.configs(); }
    };
    $('#svc-create').onclick = createServiceDialog;
    ['enable', 'disable', 'delete'].forEach((action) => {
      $(`#svc-bulk-${action}`).onclick = async () => {
        const ids = Array.from(state.selected);
        if (!ids.length) return toast(t('common.nothingSelected'), 'warn');
        if (action === 'delete' && !confirm(t('configs.bulkDeleteConfirm', { n: num(ids.length) }))) return;
        try {
          await api('/services/bulk', { method: 'POST', body: { service_ids: ids, action } });
          toast(action === 'enable' ? t('configs.enabled') : action === 'disable' ? t('configs.disabledMsg') : t('configs.deleted'), 'ok');
          Pages.configs();
        } catch (err) { toast(err.message, 'err'); }
      };
    });

    // users
    $('#usr-search').oninput = debounce(() => { state.paging.users.page = 1; Pages.users(); }, 350);
    $('#usr-status').onchange = () => { state.paging.users.page = 1; Pages.users(); };
    $('#usr-prev').onclick = () => { if (state.paging.users.page > 1) { state.paging.users.page -= 1; Pages.users(); } };
    $('#usr-next').onclick = () => {
      const ps = state.paging.users;
      if (ps.page * ps.size < ps.total) { ps.page += 1; Pages.users(); }
    };
    $('#usr-create').onclick = userCreateDialog;

    // rest
    $('#plan-create').onclick = () => planDialog(null);
    $('#pay-reload').onclick = () => Pages.payments();
    $('#pay-status').onchange = () => Pages.payments();
    $('#node-create').onclick = nodeCreateDialog;
    $('#node-railway-setup').onclick = railwayNodeDialog;
    $('#inb-node').onchange = () => Pages.inbounds();
    $('#inb-create').onclick = inboundCreateDialog;
    $('#health-reload').onclick = () => Pages.health();
    $('#alerts-reload').onclick = () => Pages.alerts();
    $('#alerts-active-only').onchange = () => Pages.alerts();
    $('#log-reload').onclick = () => Pages.logs();
    $('#log-action').oninput = debounce(() => Pages.logs(), 400);

    // ---- telegram bot configuration ------------------------------------
    $('#tg-save').onclick = async (event) => {
      const btn = event.currentTarget;
      btn.classList.add('loading');
      try { await saveTelegramConfig(); }
      catch (err) { toast(err.message, 'err'); }
      finally { btn.classList.remove('loading'); }
    };
    $('#tg-validate').onclick = async (event) => {
      const btn = event.currentTarget;
      btn.classList.add('loading');
      try { await validateTelegramToken(); }
      catch (err) { toast(err.message, 'err'); }
      finally { btn.classList.remove('loading'); }
    };
    $('#tg-test').onclick = async (event) => {
      const btn = event.currentTarget;
      btn.classList.add('loading');
      try {
        const result = await api('/telegram/test', { method: 'POST' });
        if (result.ok) toast(t('telegram.testOk', { n: num(result.delivered) }), 'ok');
        else toast(`${t('telegram.testFail')}: ${result.error}`, 'err');
      } catch (err) { toast(err.message, 'err'); }
      finally { btn.classList.remove('loading'); }
    };
    $('#tg-reset').onclick = async () => {
      if (!confirm(t('telegram.resetConfirm'))) return;
      try {
        await api('/telegram/config', { method: 'DELETE' });
        toast(t('telegram.resetDone'), 'ok');
        await Pages.telegram();
        refreshBadges();
      } catch (err) { toast(err.message, 'err'); }
    };
    $('#tg-secret-gen').onclick = async () => {
      try {
        const result = await api('/telegram/generate-secret', { method: 'POST' });
        $('#tg-secret').value = result.webhook_secret;
        toast(t('telegram.secretGenerated'), 'ok');
      } catch (err) { toast(err.message, 'err'); }
    };
    $('#tg-token-eye').onclick = () => {
      const input = $('#tg-token');
      const hidden = input.type === 'password';
      input.type = hidden ? 'text' : 'password';
      $('#tg-token-eye').innerHTML = hidden
        ? window.icon('eyeOff', { size: 16 })
        : window.icon('eye', { size: 16 });
    };
    $('#tg-reload').onclick = () => Pages.telegram();
    $('#tg-setup').onclick = async () => {
      try { const r = await api('/telegram/setup', { method: 'POST' }); toast(r.detail, 'ok'); Pages.telegram(); }
      catch (err) { toast(err.message, 'err'); }
    };
    $('#tg-unset').onclick = async () => {
      try { const r = await api('/telegram/unset-webhook', { method: 'POST' }); toast(r.detail, 'ok'); Pages.telegram(); }
      catch (err) { toast(err.message, 'err'); }
    };
    $('#backup-now').onclick = async () => {
      try { await api('/backups', { method: 'POST' }); toast(t('settings.backupCreated'), 'ok'); Pages.settings(); }
      catch (err) { toast(err.message, 'err'); }
    };
    $('#backup-prune').onclick = async () => {
      try { await api('/backups/prune', { method: 'POST' }); toast(t('settings.pruned'), 'ok'); Pages.settings(); }
      catch (err) { toast(err.message, 'err'); }
    };

    window.addEventListener('resize', debounce(() => {
      if (!state.me) return;
      if (state.page === 'dashboard') Pages.dashboard({ silent: true }).catch(() => {});
      if (state.page === 'reports') Pages.reports({ silent: true }).catch(() => {});
    }, 260));
  }

  async function loadPublicStatus() {
    try {
      const res = await fetch(`${API}/status`);
      const status = await res.json();
      $('#status-text').textContent = `${num(status.nodes_online)}/${num(status.nodes_total)} · v${status.version} · ${status.environment}`;
    } catch {
      $('#status-text').textContent = t('error.network');
    }
    try {
      const res = await fetch(`${API}/settings/public`);
      const pub = await res.json();
      if (pub.app_name) {
        $('#brand-name').textContent = pub.app_name;
        $('#login-brand').textContent = pub.app_name;
        document.title = pub.app_name;
      }
    } catch { /* optional */ }
  }

  /* ==================================================================== boot */
  async function boot() {
    wire();
    loadPublicStatus();

    if (location.pathname === '/login') {
      document.documentElement.classList.add('login-route');
      $('#app').hidden = true;
      $('#login').hidden = false;
      return;
    }

    document.documentElement.classList.remove('login-route');
    $('#login').hidden = true;
    $('#app').hidden = false;
    await restoreSession();
  }

  async function restoreSession() {
    if (!state.token && !state.refresh) {
      $('#app').hidden = true;
      $('#login').hidden = false;
      return;
    }
    try {
      if (!state.token || !await currentUserExists()) {
        if (!state.refresh || !await tryRefresh()) {
          logout();
          return;
        }
      }
      state.me = await api('/auth/me');
      await enterApp();
    } catch {
      logout();
    }
  }

  async function currentUserExists() {
    try {
      const res = await fetch(`${API}/auth/me`, { headers: { Authorization: `Bearer ${state.token}` } });
      return res.ok;
    } catch { return false; }
  }

  boot();
})();
