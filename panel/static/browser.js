import RFB from '/browser/core/rfb.js';
const status = document.getElementById('connection-status');
const screen = document.getElementById('screen');
const pasteText = document.getElementById('paste-text');
const pasteSend = document.getElementById('paste-send');
const pasteStatus = document.getElementById('paste-status');
let connected = false, busy = false;
const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
const rfb = new RFB(screen, `${protocol}//${location.host}/browser/ws`);
rfb.scaleViewport = true;
rfb.resizeSession = false;
rfb.addEventListener('connect', () => { connected = true; pasteSend.disabled = false; status.textContent = '已连接 · 请手工登录 NodeSeek，完成后将自动导入 Cookie'; });
rfb.addEventListener('disconnect', () => { connected = false; pasteSend.disabled = true; pasteText.value = ''; status.textContent = '浏览器连接已关闭。若已完成登录，请查看面板中的导入状态。'; });
rfb.addEventListener('securityfailure', () => { status.textContent = '浏览器连接失败，请关闭后重试。'; });

function message(text, error = false) { pasteStatus.textContent = text; pasteStatus.classList.toggle('error', error); }
function usePasteField() { rfb.blur(); pasteText.focus(); message('请将文本粘贴到上方临时输入框，再点击「粘贴到当前输入框」。'); }
async function sendText(text) {
  if (busy || !connected) return;
  pasteText.value = '';
  if (!text) { message('请先复制或输入要粘贴的文本。', true); return; }
  busy = true; pasteSend.disabled = true;
  // 释放远程修饰键，避免 Mac 的 ⌘ 或 Ctrl 残留；不改变容器网页的输入焦点。
  rfb.blur();
  try {
    const sessionResponse = await fetch('/api/session', {cache: 'no-store'});
    const session = await sessionResponse.json();
    if (!session.authenticated) throw new Error('管理登录已失效，请重新登录面板。');
    const response = await fetch('/api/browser/paste', {
      method: 'POST', headers: {'Content-Type': 'application/json', 'X-CSRF-Token': session.csrf},
      body: JSON.stringify({text}),
    });
    text = '';
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || '粘贴失败，请重试。');
    message('已粘贴，请在下方网页继续登录。');
    if (connected) rfb.focus();
  } catch (error) { message(error instanceof TypeError ? '网络请求失败，请重新复制并重试。' : error.message, true); }
  finally { text = ''; busy = false; pasteSend.disabled = !connected; }
}
document.getElementById('paste-form').addEventListener('submit', event => { event.preventDefault(); void sendText(pasteText.value); });
window.addEventListener('keydown', async event => {
  if (!screen.contains(event.target) || !(event.ctrlKey || event.metaKey) || event.altKey || event.key.toLowerCase() !== 'v') return;
  event.preventDefault(); event.stopImmediatePropagation();
  if (event.repeat || busy || !connected) return;
  if (!navigator.clipboard?.readText) { usePasteField(); return; }
  try { await sendText(await navigator.clipboard.readText()); }
  catch { usePasteField(); }
}, true);
window.addEventListener('paste', event => {
  if (!screen.contains(event.target)) return;
  event.preventDefault(); event.stopImmediatePropagation();
  void sendText(event.clipboardData?.getData('text/plain') || '');
}, true);
window.addEventListener('pagehide', () => { pasteText.value = ''; });
