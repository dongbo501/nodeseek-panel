'use strict';

const $ = id => document.getElementById(id);
const labels = {ok: '签到成功', already: '今日已签到', fail: '执行失败', running: '正在执行'};
const views = {
  overview: ['NodeSeek.', 'THE DAILY ORBIT', ''],
  account: ['账号连接', 'ACCOUNT CONNECTION', '管理 NodeSeek 登录会话与浏览器凭据。'],
  schedule: ['自动化', 'AUTOMATION', '安排执行时间、奖励方式与结果通知。'],
  history: ['执行记录', 'EXECUTION HISTORY', '追踪每一次签到，查看结果与执行详情。'],
};
const state = {active: false, generation: 0, csrf: '', config: null, status: null, history: [], browser: null, lastImport: null, view: 'overview', page: 1};
const baselines = {cookie: '', settings: ''};
const pending = new Set();
let refreshPromise = null, toastTimer, historySignature = '', searchTimer;
let metricsSignature = '', activeTransition = null;
const reducedMotion = matchMedia('(prefers-reduced-motion: reduce)');
let motionPaused = false;
try { motionPaused = localStorage.getItem('nodeseek-motion') === 'paused'; } catch {}
function syncMotion() {
  const enabled = !reducedMotion.matches && !motionPaused;
  document.body.dataset.motion = enabled ? 'on' : 'off';
  document.querySelectorAll('.motion-toggle').forEach(button => {
    const label = reducedMotion.matches ? '系统已关闭动态效果' : enabled ? '暂停动效' : '启用动效';
    button.setAttribute('aria-label', label); button.dataset.tooltip = label;
    button.disabled = reducedMotion.matches;
    button.replaceChildren(icon(enabled ? 'pause' : 'play'));
  });
  document.dispatchEvent(new Event('nodeseek:motion'));
}

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('icon');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  use.setAttribute('href', `/static/icons.svg#${name}`);
  svg.append(use);
  return svg;
}
function text(id, value) {
  const element = $(id), next = String(value);
  if (element.textContent === next) return;
  element.textContent = next;
  if (id.startsWith('stat-') && document.body.dataset.motion === 'on' && !reducedMotion.matches) {
    element.getAnimations().forEach(animation => animation.cancel());
    element.animate([{opacity: .3, transform: 'translateY(10px)'}, {opacity: 1, transform: 'translateY(0)'}], {duration: 450, easing: 'ease-out'});
  }
}
function success(row) { return row && (row.status === 'ok' || row.status === 'already'); }
function resultIcon(status) { return status === 'fail' ? 'circle-x' : status === 'running' ? 'loader-circle' : 'circle-check'; }
function badge(status, label) {
  const span = document.createElement('span');
  span.className = 'badge';
  if (Object.hasOwn(labels, status)) span.classList.add(status);
  span.append(icon(resultIcon(status)), document.createTextNode(label || labels[status] || '未知状态'));
  return span;
}
function toast(message, error = false) {
  text('toast-message', message);
  $('toast').classList.toggle('error', error);
  $('toast').hidden = false;
  $('toast').firstElementChild.replaceWith(icon(error ? 'circle-alert' : 'circle-check'));
  text(error ? 'alerts' : 'announcer', message);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { if (!$('toast').contains(document.activeElement)) $('toast').hidden = true; }, 6500);
}
async function api(path, method = 'GET', body) {
  const generation = state.generation;
  const response = await fetch(`/api/${path}`, {
    method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': state.csrf},
    ...(body === undefined ? {} : {body: JSON.stringify(body)}),
  });
  let data;
  try { data = await response.json(); } catch { throw new Error('服务器响应异常，请稍后重试'); }
  if (!response.ok) {
    if (response.status === 401 && path !== 'login' && generation === state.generation) showLogin();
    throw new Error(data.error || '请求失败，请稍后重试');
  }
  return data;
}
function date(value, options = {}) {
  if (!value || Number.isNaN(new Date(value).getTime())) return '—';
  return new Intl.DateTimeFormat('zh-CN', {timeZone: state.config?.timezone || 'Asia/Shanghai', ...options}).format(new Date(value));
}
function dayKey(value) {
  return date(value, {year: 'numeric', month: '2-digit', day: '2-digit'}).replaceAll('/', '-');
}
function fullDate(value) { return date(value, {year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'}); }
function timeOnly(value) { return date(value, {hour: '2-digit', minute: '2-digit', hourCycle: 'h23'}); }
function currentView() { return Object.hasOwn(views, location.hash.slice(1)) ? location.hash.slice(1) : state.view; }
function navigate(view, focus = true) {
  if (!state.active) return;
  if (activeTransition) activeTransition.skipTransition();
  const update = () => applyView(view, focus);
  if (!focus || !document.startViewTransition || reducedMotion.matches || document.body.dataset.motion !== 'on' || view === state.view) { update(); return; }
  activeTransition = document.startViewTransition(update);
  activeTransition.finished.catch(() => {}).finally(() => { activeTransition = null; });
}
function applyView(view, focus = true) {
  if (!state.active) return;
  if (!Object.hasOwn(views, view)) view = 'overview';
  state.view = view;
  document.body.dataset.view = view;
  document.querySelectorAll('[data-page]').forEach(panel => { panel.hidden = panel.dataset.page !== view; });
  document.querySelectorAll('[data-view]').forEach(link => {
    if (link.dataset.view === view) link.setAttribute('aria-current', 'page');
    else link.removeAttribute('aria-current');
  });
  const [title, kicker, description] = views[view];
  text('page-title', title);
  if (view === 'overview') {
    const dot = document.createElement('span'); dot.className = 'accent-text'; dot.textContent = '.';
    $('page-title').replaceChildren(document.createTextNode('NodeSeek'), dot);
  }
  text('breadcrumb-current', view === 'overview' ? '总览' : title);
  text('page-kicker', kicker);
  text('page-description', description || date(new Date(), {year: 'numeric', month: 'long', day: 'numeric', weekday: 'long'}));
  $('overview-actions').hidden = view !== 'overview';
  document.title = `${title} · NodeSeek`;
  if (focus) { $('page-title').focus({preventScroll: true}); window.scrollTo({top: 0, behavior: 'instant'}); }
  document.dispatchEvent(new Event('nodeseek:view'));
}
function showLogin() {
  state.active = false; state.generation += 1; state.config = null; state.status = null; state.history = []; state.browser = null; state.lastImport = null;
  $('boot').hidden = true; $('login').hidden = false; $('dashboard').hidden = true;
  $('browser-frame').removeAttribute('src');
  for (const id of ['cookie', 'user-agent', 'bark']) $(id).value = '';
  baselines.cookie = ''; baselines.settings = ''; historySignature = ''; metricsSignature = '';
  $('history-body').replaceChildren(); $('recent-list').replaceChildren();
  document.title = '登录 · NodeSeek';
  $('password').focus({preventScroll: true});
  document.dispatchEvent(new Event('nodeseek:view'));
}
function payload(group) {
  if (group === 'cookie') return {cookie: $('cookie').value, user_agent: $('user-agent').value, clear_cookie: $('clear-cookie').checked};
  return {run_at: $('run-at').value, jitter_minutes: Number($('jitter').value), timezone: $('timezone').value, enabled: $('enabled').checked, random: $('random').checked, bark_url: $('bark').value, bark_only_on_failure: $('bark-only-on-failure').checked, clear_bark_url: $('clear-bark').checked};
}
function dirty(group) { return Boolean(baselines[group] && baselines[group] !== JSON.stringify(payload(group))); }
function updateFormState(group) {
  const changed = dirty(group);
  const label = $(`${group}-save-state`);
  label.classList.toggle('dirty', changed);
  label.replaceChildren(icon(changed ? 'circle-dot' : 'check'), document.createTextNode(changed ? '有未保存的更改' : '所有更改已保存'));
  $(`${group}-reset`).disabled = !changed || pending.has(`${group}-form`);
  $(`${group}-form`).querySelector('[type=submit]').disabled = !changed || pending.has(`${group}-form`);
  if (group === 'settings') { text('enabled-label', $('enabled').checked ? '已开启' : '已暂停'); text('random-label', $('random').checked ? '随机收益' : '固定收益'); }
}
function fillForm(group) {
  const c = state.config;
  if (group === 'cookie') {
    $('cookie').value = c.cookie; $('user-agent').value = c.user_agent; $('clear-cookie').checked = false;
  } else {
    $('run-at').value = c.run_at; $('jitter').value = c.jitter_minutes; $('timezone').value = c.timezone;
    $('enabled').checked = c.enabled; $('random').checked = c.random; $('bark').value = c.bark_url;
    $('bark-only-on-failure').checked = c.bark_only_on_failure; $('clear-bark').checked = false;
  }
  baselines[group] = JSON.stringify(payload(group));
  updateFormState(group);
}
function applyConfig(c, forceGroup = null) {
  state.config = c;
  for (const group of ['cookie', 'settings']) { if (!dirty(group) || forceGroup === group || forceGroup === 'all') fillForm(group); }
  $('cookie').readOnly = c.cookie_from_env; $('clear-cookie').disabled = c.cookie_from_env;
  $('env-notice').hidden = !c.cookie_from_env;
  text('cookie-status', c.cookie_from_env ? '环境变量管理' : c.cookie_configured ? '已连接' : '尚未连接');
  $('cookie-status').className = `badge${c.cookie_configured ? ' ok' : ''}`;
  text('bark-status', c.bark_url_configured ? '已配置' : '未配置');
  $('bark-status').className = `badge${c.bark_url_configured ? ' ok' : ''}`;
  $('account-nav-dot').hidden = c.cookie_configured; $('setup-notice').hidden = c.cookie_configured;
  text('summary-account', c.cookie_configured ? '已连接' : '未连接');
  text('summary-time', c.run_at); text('summary-jitter', `0–${c.jitter_minutes} 分钟`);
  text('summary-reward', c.random ? '随机收益' : '固定收益');
  text('summary-notification', c.bark_url_configured ? (c.bark_only_on_failure ? '仅失败通知' : '所有结果') : '未开启');
  text('summary-timezone', c.timezone); text('footer-timezone', c.timezone);
  text('stat-automation', c.enabled ? '已开启' : '已暂停');
  text('schedule-description', c.enabled ? `每天 ${c.run_at} · 延迟 ${c.jitter_minutes} 分钟内` : '可随时手动签到');
  updateControls();
  if (state.status) renderStatus(state.status);
}
function updateControls() {
  $('run').disabled = pending.has('run') || Boolean(state.status?.running) || !state.config?.cookie_configured;
  $('run').querySelector('[data-button-label]').textContent = state.status?.running ? '正在签到' : '立即签到';
  $('run').setAttribute('aria-busy', String(pending.has('run') || Boolean(state.status?.running)));
  $('browser-start').disabled = pending.has('browser-start') || Boolean(state.browser?.running) || Boolean(state.config?.cookie_from_env);
}
function renderStatus(s) {
  state.status = s;
  const c = state.config;
  updateControls();
  text('schedule-status', !c.cookie_configured ? '等待连接' : !c.enabled ? '已暂停' : !s.scheduler_alive ? '调度器离线' : '自动执行中');
  $('schedule-status').className = `badge${c.enabled && c.cookie_configured && s.scheduler_alive ? ' ok' : ''}`;
  text('next-run', s.next_run ? timeOnly(s.next_run) : '--:--');
  text('next-date', s.next_run ? `${date(s.next_run, {month: 'long', day: 'numeric'})} · ${c.timezone}` : c.cookie_configured ? '开启自动签到后安排' : '连接账号后安排');
  text('last-status', s.last_result ? labels[s.last_result.status] || '未知状态' : '暂无记录');
  text('last-time', s.last_result ? fullDate(s.last_result.started_at) : '等待首次执行');
  renderToday();
  updateCountdown();
}
function updateCountdown() {
  if (!state.active || !state.status?.next_run) { text('countdown', '--:--:--'); return; }
  const seconds = Math.max(0, Math.floor((new Date(state.status.next_run) - Date.now()) / 1000));
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  text('countdown', [hours, minutes, seconds % 60].map(value => String(value).padStart(2, '0')).join(':'));
}
function updateScene(phase, days = window.nodeseekScene?.days || []) {
  const next = {phase, days};
  if (JSON.stringify(next) === JSON.stringify(window.nodeseekScene)) return;
  window.nodeseekScene = next;
  document.dispatchEvent(new CustomEvent('nodeseek:scene', {detail: next}));
}
function renderToday() {
  const today = dayKey(new Date());
  const rows = state.history.filter(row => dayKey(row.started_at) === today);
  const done = rows.find(success);
  const current = rows[0];
  const status = state.status?.running ? 'running' : done ? 'ok' : current?.status || 'none';
  const content = {
    ok: ['今日任务\n已完成。', done?.message || '今日已签到', '已完成', 'circle-check'],
    fail: ['需要一次\n重试。', current?.message || '查看记录后重试', '需要关注', 'circle-x'],
    running: ['签到进行\n中。', '正在等待 NodeSeek 返回结果', '执行中', 'loader-circle'],
    none: [state.config?.cookie_configured ? '新的一天\n已就绪。' : '连接账号\n即刻出发。', state.config?.cookie_configured ? '今日还没有签到记录' : '导入登录凭据，即可安排每日签到', '等待执行', 'circle-dashed'],
  }[status] || ['等待执行', '暂无签到结果', '等待执行', 'circle-dashed'];
  text('today-status', content[0]); text('today-message', content[1]); text('today-badge', content[2]);
  $('today-symbol').replaceChildren(icon(content[3]));
  document.querySelector('.today-summary').dataset.state = status;
  $('orbital-stage').dataset.state = status;
  text('orbit-phase', {ok: 'MISSION COMPLETE', fail: 'RETRY REQUIRED', running: 'CHECK-IN IN PROGRESS', none: 'READY TO CONNECT'}[status] || 'READY');
  updateScene(status);
  text('today-date', date(new Date(), {month: 'long', day: 'numeric', weekday: 'long'}));
}
function renderMetrics() {
  const completed = state.history.filter(row => row.status !== 'running');
  const ok = completed.filter(success).length;
  text('stat-success', ok); text('stat-failed', completed.filter(row => row.status === 'fail').length);
  text('stat-rate', completed.length ? `${Math.round(ok / completed.length * 100)}%` : '—');
  text('history-nav-count', state.history.length);
  renderToday();
  const signature = JSON.stringify([state.history, state.config?.timezone, dayKey(new Date())]);
  if (signature === metricsSignature) return;
  metricsSignature = signature;
  const today = dayKey(new Date());
  const day = new Date(`${today}T12:00:00Z`);
  const fragment = document.createDocumentFragment();
  let successfulDays = 0;
  const dayStates = [];
  for (let offset = 13; offset >= 0; offset--) {
    const d = new Date(day); d.setUTCDate(d.getUTCDate() - offset);
    const key = d.toISOString().slice(0, 10);
    const rows = state.history.filter(row => dayKey(row.started_at) === key);
    const result = rows.some(success) ? 'success' : rows.some(row => row.status === 'running') ? 'running' : rows.length ? 'fail' : 'none';
    if (result === 'success') successfulDays++;
    dayStates.push(result);
    const title = `${key}：${{success: '签到成功', fail: '执行失败', running: '正在执行', none: '无记录'}[result]}`;
    const item = document.createElement('div'); item.className = `activity-day ${result}${offset === 0 ? ' today' : ''}`; item.setAttribute('role', 'listitem'); item.setAttribute('aria-label', title); item.title = title;
    item.style.setProperty('--day-index', String(13 - offset));
    const tile = document.createElement('span'); tile.className = 'activity-tile';
    tile.append(icon({success: 'check', fail: 'x', running: 'loader-circle', none: 'minus'}[result]));
    const label = document.createElement('small'); label.textContent = offset === 0 ? '今' : String(d.getUTCDate()).padStart(2, '0'); label.setAttribute('aria-hidden', 'true');
    item.append(tile, label); fragment.append(item);
  }
  $('activity-days').replaceChildren(fragment);
  updateScene(window.nodeseekScene?.phase || 'none', dayStates);
  text('activity-summary', `近 14 天有 ${successfulDays} 天签到成功 · 最近 100 条内`);
  const recent = document.createDocumentFragment();
  for (const row of state.history.slice(0, 4)) {
    const li = document.createElement('li'); li.className = 'recent-item';
    const symbol = document.createElement('span'); symbol.className = `result-icon ${row.status}`; symbol.append(icon(resultIcon(row.status)));
    const copy = document.createElement('div'); copy.className = 'recent-copy';
    const title = document.createElement('strong'); title.textContent = labels[row.status] || '未知状态';
    const detail = document.createElement('p'); detail.textContent = row.message;
    copy.append(title, detail);
    const time = document.createElement('time'); time.dateTime = row.started_at; time.textContent = date(row.started_at, {month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
    li.append(symbol, copy, time); recent.append(li);
  }
  $('recent-list').replaceChildren(recent); $('recent-empty').hidden = state.history.length > 0;
}
function filteredHistory() {
  const query = $('history-search').value.trim().toLocaleLowerCase();
  const filter = $('history-filter').value;
  return state.history.filter(row => {
    if (filter !== 'all' && !(filter === 'success' ? success(row) : row.status === filter)) return false;
    return `${fullDate(row.started_at)} ${row.message} ${labels[row.status] || row.status} ${row.source === 'manual' ? '手动触发' : '定时任务'}`.toLocaleLowerCase().includes(query);
  });
}
function renderHistory() {
  const filtered = filteredHistory(), totalPages = Math.max(1, Math.ceil(filtered.length / 10));
  state.page = Math.min(state.page, totalPages);
  const signature = JSON.stringify([state.history, state.config?.timezone, $('history-search').value, $('history-filter').value, state.page]);
  if (signature === historySignature) return;
  historySignature = signature;
  const fragment = document.createDocumentFragment();
  for (const row of filtered.slice((state.page - 1) * 10, state.page * 10)) {
    const tr = document.createElement('tr');
    const time = document.createElement('th'); time.scope = 'row';
    const day = document.createElement('span'); day.textContent = date(row.started_at, {year: 'numeric', month: '2-digit', day: '2-digit'});
    const clock = document.createElement('small'); clock.textContent = date(row.started_at, {hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'});
    time.append(day, clock);
    const source = document.createElement('td'); source.textContent = row.source === 'manual' ? '手动触发' : '定时任务';
    const status = document.createElement('td'); status.append(badge(row.status));
    const message = document.createElement('td'); message.className = 'result-message';
    const details = document.createElement('details'); details.className = 'message-detail';
    const summary = document.createElement('summary'); summary.textContent = row.message || '查看详情';
    const full = document.createElement('p'); full.textContent = row.message || '暂无详细信息';
    details.append(summary, full); message.append(details);
    tr.append(time, source, status, message); fragment.append(tr);
  }
  $('history-body').replaceChildren(fragment); $('empty-history').hidden = filtered.length > 0;
  text('empty-history-title', state.history.length ? '没有匹配的记录' : '还没有执行记录');
  text('empty-history-description', state.history.length ? '尝试更换关键词或筛选状态。' : '完成首次签到后，即可在这里查看结果。');
  text('history-count', filtered.length ? `${(state.page - 1) * 10 + 1}–${Math.min(state.page * 10, filtered.length)} / ${filtered.length} 条 · 最近 100 条内` : '共 0 条记录');
  text('page-number', `${state.page} / ${totalPages}`);
  $('previous-page').disabled = state.page === 1; $('next-page').disabled = state.page >= totalPages;
  $('export-history').disabled = filtered.length === 0;
}
function showAccountMode(mode) {
  for (const value of ['manual', 'browser']) {
    $(`${value}-pane`).hidden = value !== mode;
    document.querySelector(`[name=account-mode][value=${value}]`).checked = mode === value;
  }
}
async function renderBrowser(b, generation = state.generation) {
  state.browser = b;
  text('browser-message', b.message + (b.running ? `（剩余 ${Math.ceil(b.remaining_seconds / 60)} 分钟）` : ''));
  $('browser-tools').hidden = !b.running; $('browser-frame-wrap').hidden = !b.ready;
  if (b.ready && !$('browser-frame').getAttribute('src')) $('browser-frame').src = '/browser/view';
  if (!b.running) $('browser-frame').removeAttribute('src');
  updateControls();
  if (b.imported_at && b.imported_at !== state.lastImport) {
    const c = await api('config');
    if (!state.active || generation !== state.generation) return;
    state.lastImport = b.imported_at;
    applyConfig(c, 'cookie'); showAccountMode('manual');
    toast('Cookie 与 User-Agent 已导入，账号连接已更新');
  }
}
function connection(connected) {
  $('connection-banner').hidden = connected;
  $('connection-dot').classList.toggle('offline', !connected);
  text('connection-label', connected ? '服务已连接' : '等待重连');
  text('sync-label', connected ? `已同步 ${timeOnly(new Date())}` : '连接中断');
}
async function refresh() {
  if (!state.active) return;
  if (refreshPromise) return refreshPromise;
  const generation = state.generation;
  const request = (async () => {
    try {
      const [s, h, b] = await Promise.all([api('status'), api('history'), api('browser')]);
      if (!state.active || generation !== state.generation) return;
      state.history = h.items;
      renderStatus(s); renderMetrics(); renderHistory(); await renderBrowser(b, generation);
      if (state.active && generation === state.generation) connection(true);
    } catch (error) {
      if (state.active && generation === state.generation) connection(false);
      throw error;
    }
  })();
  refreshPromise = request;
  try { await request; } finally { if (refreshPromise === request) refreshPromise = null; }
}
async function enter() {
  const generation = ++state.generation;
  const config = await api('config');
  if (generation !== state.generation) return;
  applyConfig(config, 'all'); state.active = true;
  $('boot').hidden = true; $('login').hidden = true; $('dashboard').hidden = false;
  navigate(currentView(), false);
  await refresh();
}
function action(id, fn, event = 'click') {
  $(id).addEventListener(event, async e => {
    e.preventDefault();
    if (pending.has(id)) return;
    const form = e.currentTarget.tagName === 'FORM' ? e.currentTarget : null;
    const button = form ? form.querySelector('[type=submit]') : e.currentTarget;
    const fields = form?.querySelector('fieldset');
    pending.add(id); button.disabled = true; button.setAttribute('aria-busy', 'true');
    if (fields) fields.disabled = true;
    try { await fn(); } catch (error) {
      if (id === 'login-form') { text('login-error', error.message); $('login-error').hidden = false; $('password').setAttribute('aria-invalid', 'true'); }
      toast(error.message, true);
    } finally {
      pending.delete(id); button.disabled = false; button.removeAttribute('aria-busy');
      if (fields) fields.disabled = false;
      if (id === 'cookie-form' || id === 'settings-form') updateFormState(id.replace('-form', ''));
      updateControls();
    }
  });
}
action('login-form', async () => {
  $('login-error').hidden = true; $('password').removeAttribute('aria-invalid');
  const session = await api('session'); state.csrf = session.csrf;
  const response = await api('login', 'POST', {password: $('password').value}); state.csrf = response.csrf;
  $('password').value = ''; await enter(); $('page-title').focus({preventScroll: true});
}, 'submit');
action('logout', async () => { await api('logout', 'POST', {}); showLogin(); });
for (const group of ['cookie', 'settings']) {
  action(`${group}-form`, async () => {
    const generation = state.generation;
    const config = await api('config', 'PUT', payload(group));
    if (!state.active || generation !== state.generation) return;
    applyConfig(config, group); toast(group === 'cookie' ? '账号连接已保存' : '自动化设置已保存');
    await refresh();
  }, 'submit');
  $(`${group}-form`).addEventListener('input', () => updateFormState(group));
  $(`${group}-form`).addEventListener('change', () => updateFormState(group));
  $(`${group}-reset`).addEventListener('click', () => { fillForm(group); toast('已恢复到上次保存的配置'); });
}
action('run', async () => { const response = await api('run', 'POST', {}); toast(response.message); await refresh(); });
for (const id of ['refresh', 'overview-refresh', 'retry']) action(id, refresh);
for (const [id, path] of [['browser-start', 'start'], ['browser-capture', 'capture'], ['browser-stop', 'stop']]) {
  action(id, async () => { const generation = state.generation; const b = await api(`browser/${path}`, 'POST', {}); if (state.active && generation === state.generation) { await renderBrowser(b, generation); await refresh(); } });
}
document.querySelectorAll('[name=account-mode]').forEach(input => input.addEventListener('change', () => showAccountMode(input.value)));
$('toast-close').addEventListener('click', () => { $('toast').hidden = true; });
$('password-toggle').addEventListener('click', () => {
  const show = $('password').type === 'password';
  $('password').type = show ? 'text' : 'password';
  $('password-toggle').setAttribute('aria-pressed', String(show));
  $('password-toggle').setAttribute('aria-label', show ? '隐藏密码' : '显示密码');
  $('password-toggle').dataset.tooltip = show ? '隐藏密码' : '显示密码';
  $('password-toggle').replaceChildren(icon(show ? 'eye-off' : 'eye'));
});
$('password').addEventListener('input', () => { $('password').removeAttribute('aria-invalid'); $('login-error').hidden = true; });
window.addEventListener('hashchange', () => { if (location.hash !== '#page-title') navigate(currentView()); });
window.addEventListener('beforeunload', event => { if (state.active && (dirty('cookie') || dirty('settings'))) { event.preventDefault(); event.returnValue = ''; } });
for (const id of ['history-search', 'history-filter']) $(id).addEventListener(id === 'history-search' ? 'input' : 'change', () => {
  state.page = 1; renderHistory(); clearTimeout(searchTimer);
  searchTimer = setTimeout(() => text('announcer', `找到 ${filteredHistory().length} 条记录`), 400);
});
for (const [id, delta] of [['previous-page', -1], ['next-page', 1]]) $(id).addEventListener('click', () => { state.page += delta; renderHistory(); });
$('export-history').addEventListener('click', () => {
  const cell = value => {
    let string = String(value ?? '');
    if (/^[\s]*[=+\-@]/.test(string) || /^[\t\r]/.test(string)) string = "'" + string;
    return '"' + string.replaceAll('"', '""') + '"';
  };
  const rows = [['执行时间', '触发方式', '状态', '结果详情'], ...filteredHistory().map(row => [fullDate(row.started_at), row.source === 'manual' ? '手动触发' : '定时任务', labels[row.status] || row.status, row.message])];
  const url = URL.createObjectURL(new Blob(['\ufeff' + rows.map(row => row.map(cell).join(',')).join('\r\n')], {type: 'text/csv;charset=utf-8'}));
  const link = document.createElement('a'); link.href = url; link.download = `nodeseek-history-${dayKey(new Date())}.csv`; document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000); toast('执行记录已导出');
});
const themeMeta = document.querySelector('meta[name="color-scheme"]');
function syncThemeControl() { $('theme-select').value = ['light', 'dark'].includes(themeMeta.content) ? themeMeta.content : 'system'; }
$('theme-select').addEventListener('change', () => {
  const value = $('theme-select').value; themeMeta.content = value === 'system' ? 'light dark' : value;
  try { if (value === 'system') localStorage.removeItem('nodeseek-theme'); else localStorage.setItem('nodeseek-theme', value); } catch { /* The visible theme still updates without storage. */ }
});
window.addEventListener('storage', event => {
  if (event.key === 'nodeseek-theme' || event.key === null) { themeMeta.content = ['light', 'dark'].includes(event.newValue) ? event.newValue : 'light dark'; syncThemeControl(); }
});
syncThemeControl();
document.querySelectorAll('.motion-toggle').forEach(button => button.addEventListener('click', () => {
  motionPaused = !motionPaused;
  try { if (motionPaused) localStorage.setItem('nodeseek-motion', 'paused'); else localStorage.removeItem('nodeseek-motion'); } catch {}
  syncMotion();
}));
reducedMotion.addEventListener('change', syncMotion);
syncMotion();
(async () => {
  try { const session = await api('session'); state.csrf = session.csrf; if (session.authenticated) await enter(); else showLogin(); }
  catch (error) { if (!state.active) showLogin(); toast(error.message, true); }
})();
setInterval(() => { if (!document.hidden) refresh().catch(() => {}); }, 5000);
setInterval(() => { if (!document.hidden && state.active && state.view === 'overview') updateCountdown(); }, 1000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh().catch(() => {}); });
