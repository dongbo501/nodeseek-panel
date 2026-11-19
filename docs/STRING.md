# String 签到接口分析

分析日期：2026-11-19。站点：[https://www.string.ink/dashboard](https://www.string.ink/dashboard)。

## 依据

- 登录后的控制台显示普通签到「今日已签 +0.02」、本轮 1 / 7 天；签到 Plus 根据当日 Token 用量自动发放。
- 控制台公开资源 `assets/index-s4hZiBZO.js` 定义 API 前缀 `/api/v1`、Bearer 认证、登录与刷新凭证处理。
- `assets/checkin-BmmZFmwx.js` 定义 `status`、`sign`、`history`、`reveal`；`assets/DashboardView-aSy7V-M1.js` 定义状态字段和签到按钮行为。
- 对 `GET /api/v1/user/checkin` 发送不带凭证的请求，实际返回 HTTP 401、`UNAUTHORIZED`、`Authorization header is required`。

这说明存在网页使用的内部 HTTP 接口；没有发现必须操作页面才能完成的签到步骤，也没有在普通签到处理函数中发现验证码参数。这不代表网站承诺接口稳定，或服务器永远不会增加验证。

## 请求与认证

| 用途 | 方法与路径 | 参数 |
| --- | --- | --- |
| 状态 | `GET /api/v1/user/checkin` | `Authorization: Bearer <auth_token>` |
| 普通签到 | `POST /api/v1/user/checkin` | 同上；前端没有传请求体 |
| 签到记录 | `GET /api/v1/user/checkin/history?page=1` | 同上 |
| 登录续期 | `POST /api/v1/auth/refresh` | JSON `{"refresh_token":"…"}` |
| 开启奖励券 | `POST /api/v1/user/checkin/tickets/<id>` | 前端已发现，本项目不自动调用 |

前端在 `localStorage.auth_token` 存访问凭证，在 `localStorage.refresh_token` 存刷新凭证；模型 API 密钥是另一套功能。邮箱、手机号和第三方登录最终使用相同的 String 凭证机制。请求收到 401 后，前端用刷新凭证获得新的 `access_token`、`refresh_token`、`expires_in` 并覆盖旧值。

前端按 `{code: 0, data: ...}` 解包响应。状态对象包含 `day`、`rules.basic.enabled`、`basic.today`（积分的百分之一）、`basic.progress`、`basic.days`、`tickets` 等字段。`basic.today` 非零时显示今日已签；例如值 `2` 显示 `0.02` 积分。

## 适配与验证边界

- 运行时先查询状态，已签到直接返回；需要签到时提交，再查询确认。临时网络错误后重试也先查状态。字段或响应包不符合预期时停止，不猜测成功。
- 登录过期才刷新；刷新后原子保存两个凭证，避免重启丢失轮换值。保存时检查当前凭证仍与任务启动时一致，防止用户换号被旧任务覆盖。
- 仅固定 HTTPS 站点，不跟随重定向；String 请求不携带 NodeSeek Cookie。原始远端错误响应不写入历史或推送。
- Plus 用量任务与奖励券开启不在普通签到范围内。
- 本次账号当天已手动签到。没有在真实账号上重新提交签到，也没有对真实账号测试 Token 轮换；成功提交和续期路径由基于前端契约的离线测试覆盖。真实网站未来的响应结构和服务端防护仍需上线后验证。
- 本地 Python 单元测试和面板交互检查通过；在 `linux/amd64` Docker 镜像中以 `--network none` 运行全部 57 项测试通过（无跳过），包含真实 Chromium 的 NodeSeek Cookie、String localStorage 凭证导入与进程/临时文件清理。浏览器测试中的 String 页面由请求拦截提供离线内容，不连接真实网站。
