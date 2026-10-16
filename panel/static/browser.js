import RFB from '/browser/core/rfb.js';
const status = document.getElementById('connection-status');
const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
const rfb = new RFB(document.getElementById('screen'), `${protocol}//${location.host}/browser/ws`);
rfb.scaleViewport = true;
rfb.resizeSession = false;
rfb.addEventListener('connect', () => { status.textContent = '已连接 · 请手工登录 NodeSeek，完成后将自动导入 Cookie'; });
rfb.addEventListener('disconnect', () => { status.textContent = '浏览器连接已关闭。若已完成登录，请查看面板中的导入状态。'; });
rfb.addEventListener('securityfailure', () => { status.textContent = '浏览器连接失败，请关闭后重试。'; });
