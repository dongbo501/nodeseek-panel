FROM python:3.12-slim-bookworm
ARG SOURCE_URL=https://github.com/dongbo501/nodeseek-panel
ARG REVISION=unknown
LABEL org.opencontainers.image.title="NodeSeek Panel" \
      org.opencontainers.image.description="NodeSeek 签到管理面板，支持手动 Cookie 和容器浏览器登录" \
      org.opencontainers.image.source=$SOURCE_URL \
      org.opencontainers.image.revision=$REVISION
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TZ=Asia/Shanghai DATA_DIR=/data HOME=/home/app
RUN apt-get update && apt-get install -y --no-install-recommends \
      chromium xvfb x11vnc novnc fonts-noto-cjk tzdata tini ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --create-home app \
    && mkdir /data && chown app:app /data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app nodeseek_signin.py ./
COPY --chown=app:app panel ./panel
USER 10001:10001
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3)"
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "-m", "panel"]
