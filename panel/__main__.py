import os

# 单个 worker 保证调度器和浏览器会话只运行一份；线程处理网页与 VNC。
os.execvp("gunicorn", ["gunicorn", "panel.web:create_app()", "--bind", "0.0.0.0:8080",
                       "--workers", "1", "--threads", "16", "--timeout", "120",
                       "--graceful-timeout", "25", "--access-logfile", "-",
                       "--error-logfile", "-", "--forwarded-allow-ips", ""])
