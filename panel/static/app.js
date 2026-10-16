'use strict';
const $ = id => document.getElementById(id);
let running = false, browserRunning = false;
let csrf = '', config = null, active = false, polling = false, lastImport = null, timer;
const labels = {ok: '签到成功', already: '今日已签到', fail: '执行失败', running: '正在执行'};
function toast(message, error = false) { const el = $('toast'); el.textContent = message; el.classList.toggle('error', error); el.hidden = false; clearTimeout(timer); timer = setTimeout(() => el.hidden = true, 6000); }
async function api(path, method = 'GET', body) {
  const r = await fetch('/api/' + path, {method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, ...(body !== undefined ? {body: JSON.stringify(body)} : {})});
  const data = await r.json();
  if (!r.ok) { if (r.status === 401 && path !== 'login') showLogin(); throw new Error(data.error || '请求失败'); }
  return data;
}
function showLogin() { active = false; config = null; lastImport = null; for (const id of ['cookie', 'user-agent', 'bark']) $(id).value = ''; $('login').hidden = false; $('dashboard').hidden = true; $('browser-frame').removeAttribute('src'); }
function date(value, timeOnly = false) { if (!value) return '—'; return new Intl.DateTimeFormat('zh-CN', {timeZone: config?.timezone || 'Asia/Shanghai', ...(timeOnly ? {hour: '2-digit', minute: '2-digit', hour12: false} : {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})}).format(new Date(value)); }
function fillConfig(c) {
  config = c; $('run-at').value = c.run_at; $('jitter').value = c.jitter_minutes; $('timezone').value = c.timezone;
  $('enabled').checked = c.enabled; $('random').checked = c.random; $('user-agent').value = c.user_agent;
  $('cookie').value = c.cookie; $('bark').value = c.bark_url; $('clear-cookie').checked = false; $('clear-bark').checked = false;
  $('cookie-status').textContent = c.cookie_from_env ? '环境变量配置' : c.cookie_configured ? 'Cookie 已配置' : '待配置 Cookie';
  $('cookie-status').className = 'badge' + (c.cookie_configured ? ' ok' : '');
  $('bark-status').textContent = c.bark_url_configured ? '已配置' : '可选';
  $('cookie').readOnly = c.cookie_from_env; $('clear-cookie').disabled = c.cookie_from_env;
  $('browser-start').disabled = c.cookie_from_env;
}
function renderStatus(s) {
  running = s.running;
  $('run').disabled = s.running || !config?.cookie_configured;
  $('run').textContent = s.running ? '正在执行…' : '立即签到 ↗';
  $('schedule-status').textContent = !config.cookie_configured ? '等待配置' : config.enabled ? '已开启' : '已暂停';
  $('schedule-description').textContent = config.enabled ? `每天 ${config.run_at} · 延迟 0–${config.jitter_minutes} 分钟` : '手动签到仍然可用';
  $('next-run').textContent = s.next_run ? date(s.next_run, true) : '—';
  $('next-date').textContent = s.next_run ? `${new Intl.DateTimeFormat('zh-CN',{timeZone:config.timezone,dateStyle:'medium'}).format(new Date(s.next_run))} · ${config.timezone}` : '开启自动签到并配置 Cookie 后安排';
  $('last-status').textContent = s.last_result ? labels[s.last_result.status] : '暂无记录';
  $('last-time').textContent = s.last_result ? date(s.last_result.started_at) : '每一次执行都有迹可循';
}
function renderHistory(items) {
  $('history-body').replaceChildren(); $('empty-history').hidden = items.length > 0;
  for (const row of items) {
    const tr = document.createElement('tr');
    for (const text of [date(row.started_at), row.source === 'manual' ? '手动触发' : '定时任务']) { const td = document.createElement('td'); td.textContent = text; tr.append(td); }
    const status = document.createElement('td'), badge = document.createElement('span');
    badge.className = 'badge ' + (row.status === 'already' ? 'ok' : row.status); badge.textContent = labels[row.status] || row.status; status.append(badge); tr.append(status);
    const message = document.createElement('td'); message.textContent = row.message; tr.append(message); $('history-body').append(tr);
  }
}
async function renderBrowser(b) {
  browserRunning = b.running;
  $('browser-message').textContent = b.message + (b.running ? `（剩余 ${Math.ceil(b.remaining_seconds / 60)} 分钟）` : '');
  $('browser-tools').hidden = !b.running; $('browser-start').disabled = b.running || config?.cookie_from_env;
  $('browser-frame-wrap').hidden = !b.ready;
  if (b.ready && !$('browser-frame').getAttribute('src')) $('browser-frame').src = '/browser/view';
  if (!b.running) $('browser-frame').removeAttribute('src');
  if (b.imported_at && b.imported_at !== lastImport) { lastImport = b.imported_at; fillConfig(await api('config')); showAccountMode('manual'); toast('已自动导入 Cookie 和 User-Agent，当前值已显示'); }
}
async function refresh() { if (!active || polling) return; polling = true; try { const [s,h,b] = await Promise.all([api('status'),api('history'),api('browser')]); renderStatus(s); renderHistory(h.items); await renderBrowser(b); } finally { polling = false; } }
async function enter() { fillConfig(await api('config')); active = true; $('login').hidden = true; $('dashboard').hidden = false; await refresh(); }
function action(id, fn, event = 'click') { $(id).addEventListener(event, async e => { e.preventDefault(); const button = e.currentTarget.tagName === 'FORM' ? e.currentTarget.querySelector('button[type=submit]') : e.currentTarget; button.disabled = true; try { await fn(); } catch (e) { toast(e.message, true); } finally { button.disabled = id === "run" ? running || !config?.cookie_configured : id === "browser-start" ? browserRunning || config?.cookie_from_env : false; } }); }
action('login-form', async () => { const s = await api('session'); csrf = s.csrf; const r = await api('login', 'POST', {password: $('password').value}); csrf = r.csrf; $('password').value = ''; await enter(); }, 'submit');
action('logout', async () => { await api('logout', 'POST', {}); showLogin(); });
action('cookie-form', async () => { fillConfig(await api('config', 'PUT', {cookie: $('cookie').value, user_agent: $('user-agent').value, clear_cookie: $('clear-cookie').checked})); toast('登录配置已保存'); await refresh(); }, 'submit');
action('settings-form', async () => { fillConfig(await api('config', 'PUT', {run_at: $('run-at').value, jitter_minutes: Number($('jitter').value), timezone: $('timezone').value, enabled: $('enabled').checked, random: $('random').checked, bark_url: $('bark').value, clear_bark_url: $('clear-bark').checked})); toast('设置已保存，签到计划已更新'); await refresh(); }, 'submit');
action('run', async () => { const r = await api('run','POST',{}); toast(r.message); await refresh(); });
action('refresh', refresh);
action('browser-start', async () => { await renderBrowser(await api('browser/start','POST',{})); });
action('browser-capture', async () => { await renderBrowser(await api('browser/capture','POST',{})); await refresh(); });
action('browser-stop', async () => { await renderBrowser(await api('browser/stop','POST',{})); });
function showAccountMode(mode) { for (const tab of ['manual','browser']) { $(tab+'-pane').hidden = tab !== mode; $(tab+'-tab').classList.toggle('selected',tab === mode); $(tab+'-tab').setAttribute('aria-selected',String(tab === mode)); } }
for (const mode of ['manual', 'browser']) $(mode+'-tab').addEventListener('click', () => showAccountMode(mode));
(async () => { try { const s = await api('session'); csrf = s.csrf; if (s.authenticated) await enter(); else showLogin(); } catch(e) { showLogin(); toast(e.message,true); } })();
setInterval(() => refresh().catch(e => { if(active) toast(e.message,true); }),5000);
