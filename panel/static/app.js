'use strict';
const $ = id => document.getElementById(id);
let running = false, browserRunning = false;
let csrf = '', config = null, active = false, polling = false, lastImport = null, timer;
let selectedSite = 'nodeseek', actions = 0;
const siteNames = {nodeseek: 'NodeSeek', string: 'String'};
const configured = () => Boolean(config?.credential_configured);
const labels = {ok: '签到成功', already: '今日已签到', fail: '执行失败', running: '正在执行'};
function toast(message, error = false) { const el = $('toast'); el.textContent = message; el.classList.toggle('error', error); el.hidden = false; clearTimeout(timer); timer = setTimeout(() => el.hidden = true, 6000); }
async function api(path, method = 'GET', body) {
  const r = await fetch('/api/' + path + '?site=' + encodeURIComponent(selectedSite), {method, headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf}, ...(body !== undefined ? {body: JSON.stringify(body)} : {})});
  const data = await r.json();
  if (!r.ok) { if (r.status === 401 && path !== 'login') showLogin(); throw new Error(data.error || '请求失败'); }
  return data;
}
function clearCredentials() { for (const id of ['cookie', 'user-agent', 'bark', 'access-token', 'refresh-token']) $(id).value = ''; }
function showLogin() { active = false; config = null; lastImport = null; clearCredentials(); $('login').hidden = false; $('dashboard').hidden = true; $('browser-frame').removeAttribute('src'); }
function date(value, timeOnly = false) { if (!value) return '—'; return new Intl.DateTimeFormat('zh-CN', {timeZone: config?.timezone || 'Asia/Shanghai', ...(timeOnly ? {hour: '2-digit', minute: '2-digit', hour12: false} : {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false})}).format(new Date(value)); }
function fillConfig(c) {
  config = c; $('run-at').value = c.run_at; $('jitter').value = c.jitter_minutes; $('timezone').value = c.timezone;
  $('enabled').checked = c.enabled; $('random').checked = c.random; $('user-agent').value = c.user_agent;
  $('cookie').value = c.cookie; $('bark').value = c.bark_url; $('clear-cookie').checked = false; $('clear-bark').checked = false;
  $('bark-only-on-failure').checked = c.bark_only_on_failure;
  const isString = selectedSite === 'string';
  $('access-token').value = c.access_token || ''; $('refresh-token').value = c.refresh_token || ''; $('clear-tokens').checked = false;
  $('cookie-field').hidden = isString; $('clear-cookie-label').hidden = isString; $('string-fields').hidden = !isString; $('random-row').hidden = isString;
  $('site-description').textContent = isString ? '管理 String 普通签到；按网站 UTC+8 日期判断，今日已签到会自动跳过。' : '管理你的 NodeSeek 每日签到，把时间留给发现。';
  $('account-description').textContent = isString ? '导入同一登录会话的两个 Token，支持自动续期。签到 Plus 由网站根据用量自动发放，奖励券请到网站手动开启。' : '已有 Cookie 可直接填写，也可以在容器内登录后自动导入。';
  $('browser-guide-text').textContent = `打开浏览器 → 手工登录 ${siteNames[selectedSite]} → 自动导入${isString ? '两个登录 Token' : ' Cookie'}。验证码由你完成，检测到登录会话后浏览器自动关闭。`;
  $('browser-frame').title = `${siteNames[selectedSite]} 容器登录浏览器`;
  $('cookie-status').textContent = c.cookie_from_env ? '环境变量配置' : configured() ? '登录凭证已配置' : '待配置登录凭证';
  $('cookie-status').className = 'badge' + (configured() ? ' ok' : '');
  $('bark-status').textContent = c.bark_url_configured ? '已配置' : '可选';
  $('cookie').readOnly = c.cookie_from_env; $('clear-cookie').disabled = c.cookie_from_env;
  $('browser-start').disabled = c.cookie_from_env;
}
function renderStatus(s) {
  running = s.running;
  $('run').disabled = s.running || !configured();
  $('run').textContent = s.running ? '正在执行…' : '立即签到 ↗';
  $('schedule-status').textContent = !configured() ? '等待配置' : config.enabled ? '已开启' : '已暂停';
  $('schedule-description').textContent = config.enabled ? `每天 ${config.run_at} · 延迟 0–${config.jitter_minutes} 分钟` : '手动签到仍然可用';
  $('next-run').textContent = s.next_run ? date(s.next_run, true) : '—';
  $('next-date').textContent = s.next_run ? `${new Intl.DateTimeFormat('zh-CN',{timeZone:config.timezone,dateStyle:'medium'}).format(new Date(s.next_run))} · ${config.timezone}` : '开启自动签到并配置登录凭证后安排';
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
  const own = b.site === selectedSite;
  if (!own) {
    $('browser-message').textContent = b.running ? `${siteNames[b.site]} 的登录浏览器正在运行，请切换站点完成登录或关闭它。` : '浏览器按需启动，10 分钟后自动关闭。建议使用桌面设备。';
    $('browser-tools').hidden = true; $('browser-frame-wrap').hidden = true; $('browser-frame').removeAttribute('src');
    $('browser-start').disabled = b.running || config?.cookie_from_env;
    return;
  }
  $('browser-message').textContent = b.message + (b.running ? `（剩余 ${Math.ceil(b.remaining_seconds / 60)} 分钟）` : '');
  $('browser-tools').hidden = !b.running; $('browser-start').disabled = b.running || config?.cookie_from_env;
  $('browser-frame-wrap').hidden = !b.ready;
  if (b.ready && !$('browser-frame').getAttribute('src')) $('browser-frame').src = '/browser/view';
  if (!b.running) $('browser-frame').removeAttribute('src');
  if (b.imported_at && b.imported_at !== lastImport) { const site = selectedSite; lastImport = b.imported_at; const c = await api('config'); if (!active || site !== selectedSite) return; fillConfig(c); showAccountMode('manual'); toast('已自动导入登录凭证和 User-Agent，当前值已显示'); }
}
async function refresh() { if (!active || polling) return; polling = true; const site = selectedSite; try { const [s,h,b] = await Promise.all([api('status'),api('history'),api('browser')]); if (!active || site !== selectedSite) return; renderStatus(s); renderHistory(h.items); await renderBrowser(b); } finally { polling = false; if (active && site !== selectedSite) void refresh().catch(e => toast(e.message, true)); } }
async function enter() { fillConfig(await api('config')); active = true; $('login').hidden = true; $('dashboard').hidden = false; await refresh(); }
function action(id, fn, event = 'click') { $(id).addEventListener(event, async e => { e.preventDefault(); const button = e.currentTarget.tagName === 'FORM' ? e.currentTarget.querySelector('button[type=submit]') : e.currentTarget; button.disabled = true; actions++; $('site').disabled = true; try { await fn(); } catch (e) { toast(e.message, true); } finally { actions--; $('site').disabled = actions > 0; button.disabled = id === 'run' ? running || !configured() : id === 'browser-start' ? browserRunning || config?.cookie_from_env : false; } }); }
action('login-form', async () => { const s = await api('session'); csrf = s.csrf; const r = await api('login', 'POST', {password: $('password').value}); csrf = r.csrf; $('password').value = ''; await enter(); }, 'submit');
action('logout', async () => { await api('logout', 'POST', {}); showLogin(); });
action('cookie-form', async () => {
  const credentials = selectedSite === 'string' ? {access_token: $('access-token').value, refresh_token: $('refresh-token').value, clear_access_token: $('clear-tokens').checked, clear_refresh_token: $('clear-tokens').checked} : {cookie: $('cookie').value, clear_cookie: $('clear-cookie').checked};
  // 不回写未编辑的 Token，避免覆盖后台刚轮换的登录凭证。
  for (const key of ['access_token', 'refresh_token']) if (credentials[key] === config?.[key]) delete credentials[key];
  fillConfig(await api('config', 'PUT', {...credentials, user_agent: $('user-agent').value})); toast('登录配置已保存'); await refresh();
}, 'submit');
action('settings-form', async () => { fillConfig(await api('config', 'PUT', {run_at: $('run-at').value, jitter_minutes: Number($('jitter').value), timezone: $('timezone').value, enabled: $('enabled').checked, random: $('random').checked, bark_url: $('bark').value, bark_only_on_failure: $('bark-only-on-failure').checked, clear_bark_url: $('clear-bark').checked})); toast('设置已保存，签到计划已更新'); await refresh(); }, 'submit');
action('run', async () => { const r = await api('run','POST',{}); toast(r.message); await refresh(); });
action('refresh', refresh);
action('browser-start', async () => { await renderBrowser(await api('browser/start','POST',{})); });
action('browser-capture', async () => { await renderBrowser(await api('browser/capture','POST',{})); await refresh(); });
action('browser-stop', async () => { await renderBrowser(await api('browser/stop','POST',{})); });
function showAccountMode(mode) { for (const tab of ['manual','browser']) { $(tab+'-pane').hidden = tab !== mode; $(tab+'-tab').classList.toggle('selected',tab === mode); $(tab+'-tab').setAttribute('aria-selected',String(tab === mode)); } }
for (const mode of ['manual', 'browser']) $(mode+'-tab').addEventListener('click', () => showAccountMode(mode));
$('site').addEventListener('change', async () => {
  selectedSite = $('site').value; active = false; config = null; lastImport = null;
  clearCredentials(); $('browser-frame').removeAttribute('src'); $('browser-frame-wrap').hidden = true;
  $('run').disabled = true; $('site').disabled = true; renderHistory([]); showAccountMode('manual');
  for (const id of ['schedule-status', 'next-run', 'last-status']) $(id).textContent = '—';
  try { await enter(); } catch (e) { toast(e.message, true); } finally { $('site').disabled = actions > 0; }
});
(async () => { try { const s = await api('session'); csrf = s.csrf; if (s.authenticated) await enter(); else showLogin(); } catch(e) { showLogin(); toast(e.message,true); } })();
setInterval(() => refresh().catch(e => { if(active) toast(e.message,true); }),5000);
