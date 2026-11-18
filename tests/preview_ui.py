"""Local UI fixture. Uses temporary data and never calls NodeSeek or Bark."""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['ADMIN_PASSWORD'] = 'preview-only-password'
os.environ['COOKIE_SECURE'] = 'false'
os.environ.pop('NODESEEK_COOKIE', None)

from panel.web import create_app


def main():
    with tempfile.TemporaryDirectory(prefix='nodeseek-ui-') as directory:
        app = create_app(directory, start_scheduler=False, runner=lambda cfg, **kw: ('ok', '签到成功，获得 5 个鸡腿'))
        store = app.extensions['config_store']
        service = app.extensions['signin_service']
        store.update({'cookie': 'session=preview-fixture', 'run_at': '08:30', 'jitter_minutes': 15})
        # No outbound notification even when a UI test saves a Bark URL.
        import panel.service
        panel.service.notify_check_in = lambda *args: None
        now = datetime.now(timezone.utc)
        for offset in range(26, -1, -1):
            at = (now - timedelta(days=offset)).isoformat()
            status = 'fail' if offset in (3, 8, 20) else 'already' if offset == 4 else 'ok'
            message = '请求超时，请检查网络连接后重试' if status == 'fail' else '今日已完成签到' if status == 'already' else f'签到成功，获得 {5 + offset % 8} 个鸡腿'
            service.db.execute('INSERT INTO history(started_at,finished_at,source,status,message) VALUES (?,?,?,?,?)',
                               (at, at, 'manual' if offset % 5 == 0 else 'scheduled', status, message))
        service.db.commit()
        service.refresh_schedule()
        service.start()
        try:
            app.run(host='127.0.0.1', port=int(os.environ.get('PREVIEW_PORT', '8765')), threaded=True, use_reloader=False)
        finally:
            service.shutdown()
            service.db.close()


if __name__ == '__main__':
    main()
