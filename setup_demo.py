"""Run with the project venv after pip install -r backend/requirements-dev.txt."""
import json
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / 'backend'
sys.path.insert(0, str(BACKEND))
from balance_handoff.prepare import prepare
from werkzeug.security import generate_password_hash

if __name__ == '__main__':
    config = BACKEND / 'config.local.json'
    if not config.exists():
        password=secrets.token_urlsafe(15)
        config.write_text(json.dumps({'dashboard_key':secrets.token_urlsafe(32),
            'device_keys':{'esp32-demo':secrets.token_urlsafe(32)},'judge_username':'juri',
            'judge_password':password,'judge_password_hash':generate_password_hash(password)}, indent=2)+'\n')
        print('Konfigurasi lokal dibuat; jangan unggah config.local.json ke publik.')
    existing=json.loads(config.read_text())
    if not existing.get('judge_password_hash'):
        password=secrets.token_urlsafe(15)
        existing.update(judge_username='juri',judge_password=password,judge_password_hash=generate_password_hash(password))
        config.write_text(json.dumps(existing,indent=2)+'\n')
        print('Akun demo juri lokal ditambahkan; kunci operator/perangkat dipertahankan.')
    if not (BACKEND/'.state'/'active.json').exists():
        prepare(BACKEND/'.state')
    else:
        from balance_handoff import create_service
        create_service(BACKEND/'.state')
        print('Model lokal siap dan integritas artefaknya terverifikasi.')
    print('SETUP_READY. Jalankan backend/app.py, lalu buka http://127.0.0.1:5000.')
    print('Frontend hasil build sudah disertakan; tidak perlu npm install/build untuk mencoba paket ini.')
    print('Untuk masuk dasbor: python show_access.py dashboard')
    print('Untuk akun demo juri lokal: python show_access.py judge')
