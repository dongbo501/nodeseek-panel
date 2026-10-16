# 部署与维护

## 数据目录

Compose 默认使用命名卷，容器内路径 `/data`：

| 文件 | 用途 |
| --- | --- |
| `config.json` | Cookie、签到配置、Bark 地址 |
| `history.sqlite3`（及 WAL 文件） | 执行历史、持久化计划 |
| `session.key` | 管理面板会话签名密钥 |
| `auth.json` | 未提供 ADMIN_PASSWORD 时生成的管理密码哈希 |

所有文件都属于 UID/GID `10001:10001`。容器浏览器使用 `/tmp` 临时资料目录，导入、手工关闭或 10 分钟超时后清理；容器重启会结束浏览器会话。

如需在 NAS 使用绑定目录，把 Compose 的 volumes 改为 `./data:/data`，先建立并授权：

```bash
mkdir -p data
sudo chown -R 10001:10001 data
sudo chmod 700 data
```

## 备份与恢复

备份前先停止容器，确保 SQLite 数据一致。备份包含 Cookie 等敏感信息，请存放在受保护位置。

```bash
docker compose stop
mkdir -p backup
docker cp nodeseek-panel:/data/. ./backup/
docker compose start
```

恢复到已创建并停止的容器：

```bash
docker compose stop
docker cp ./backup/. nodeseek-panel:/data/
docker compose run --rm --user 0 --entrypoint sh nodeseek \
  -c 'chown -R 10001:10001 /data && chmod 700 /data && find /data -type f -exec chmod 600 {} \;'
docker compose up -d
```

`docker compose down` 保留命名卷；`docker compose down -v` 会删除卷数据，请不要在日常更新时使用。

## HTTPS 反向代理

默认绑定 `127.0.0.1:8080`，可以在宿主机使用现有 Nginx/Caddy。以 Nginx 为例，在你的 HTTPS server 块内加入：

```nginx
location / {
    proxy_pass http://127.0.0.1:8080;
    proxy_http_version 1.1;
    proxy_set_header Host $http_host;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 660s;
    proxy_send_timeout 660s;
    proxy_buffering off;
}
```

证书由你的现有代理管理。将 `.env` 中 `COOKIE_SECURE=true` 后执行 `docker compose up -d`。使用子域名根路径部署，例如 `https://signin.example.com/`；目前不支持挂在 `/nodeseek/` 子路径。

WebSocket 路径是 `/browser/ws`。若面板正常但内嵌浏览器无法连接，检查代理是否保留 Host、Origin 和 Upgrade 头，以及是否支持 WebSocket。不要把 `5900` 或 `9222` 映射到宿主机。

应用默认不信任代理提供的客户端 IP，代理后的失败登录次数可能按同一来源计数。避免直接开启信任任意 `X-Forwarded-*` 的配置。

## 修改或重置管理密码

修改 `.env` 中的 `ADMIN_PASSWORD`（至少 12 字符），然后：

```bash
docker compose up -d --force-recreate
```

已有会话会失效，需要重新登录。忘记密码时也可使用此方法，无需删除数据。未设置环境变量时，首次密码只在首次启动日志显示一次，后续重启使用原密码哈希；日志丢失时请通过环境变量设置新密码。

## 常见问题

**面板显示 Cookie 已配置但签到失败**

点击「立即签到」并查看具体记录。Cookie 可能已过期，或被 Cloudflare 绑定到旧 IP/User-Agent。使用容器浏览器重新登录并再次验证。容器浏览器的网络出口应与签到容器一致；项目不会自动解决验证码，也不能保证通过所有站点防护。

**容器浏览器空白或启动失败**

确认使用完整官方镜像、有足够内存和 `/tmp` 空间，并设置 `shm_size: 512mb`。等待数秒初始化。未完成的浏览器 10 分钟后自动关闭，重新打开即可。

**浏览器导入后签到仍提示 Cloudflare**

网页登录与 Python HTTP 请求的特征不同。可检查网络/出口 IP、手工更新 Cookie，或稍后再试；不应通过关闭 TLS 校验解决。

**浏览器验证码需要交互**

在嵌入浏览器里手工完成。程序不替你解验证码。如果站点拒绝数据中心 IP，需要换到站点接受的网络环境。

**浏览器导入按钮不可用**

检查是否设置 `NODESEEK_COOKIE`。环境变量优先级高于面板配置；移除此变量并重新创建容器即可恢复编辑。

**运行时间不对**

面板时区设置独立于宿主机时区。确保填的是 IANA 时区，如 `Asia/Shanghai`，并注意随机延迟。修改后的计划若开始时间已过，会安排到次日。

**Bark 通知没有到达**

确认完整 HTTPS 地址与设备密钥，检查容器日志中是否有通知失败。Bark 失败不影响签到结果，密钥不会写入日志。

**健康检查**

`GET /healthz` 不需要登录，仅返回服务状态；`docker compose ps` 可以查看健康状态。其余配置、执行和浏览器接口需要登录。

## 发布你自己的镜像

Fork 仓库并允许 Actions 运行。在仓库 Settings → Actions → General 中允许工作流写 packages。推送 `main` 或手工执行 `Test and publish container` 工作流。工作流使用 GitHub 自带 `GITHUB_TOKEN`，不需要把个人令牌写入源码。

首次 GHCR 包可能默认 Private，在 GitHub 个人 Packages → nodeseek-panel → Package settings 中设为 Public。修改 Compose 镜像地址为 `ghcr.io/你的用户名/nodeseek-panel:latest`。

手工构建发布（先 `docker login ghcr.io`）：

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
  --build-arg SOURCE_URL=https://github.com/你的用户名/nodeseek-panel \
  -t ghcr.io/你的用户名/nodeseek-panel:latest --push .
```

请通过 `--password-stdin` 或交互方式登录镜像仓库，不要把令牌写到命令参数、Dockerfile、仓库文件或截图中。
