import json
import os
from pathlib import Path
from werkzeug.security import generate_password_hash

ROOT=Path(__file__).resolve().parent

def settings():
    path=ROOT/'config.local.json'
    config=json.loads(path.read_text()) if path.exists() else {}
    if os.getenv('DASHBOARD_KEY'):config['dashboard_key']=os.environ['DASHBOARD_KEY']
    if os.getenv('DEVICE_KEY'):config['device_keys']={'esp32-demo':os.environ['DEVICE_KEY']}
    if os.getenv('JUDGE_PASSWORD'):
        password=os.environ['JUDGE_PASSWORD']
        if len(password)<12:raise ValueError('JUDGE_PASSWORD minimal 12 karakter.')
        config.update(judge_username=os.getenv('JUDGE_USERNAME','juri'),judge_password_hash=generate_password_hash(password))
    if 'dashboard_key' not in config or not config.get('device_keys'):raise ValueError('Jalankan setup_demo.py atau isi DASHBOARD_KEY dan DEVICE_KEY pada hosting.')
    config['enable_actuation']=os.getenv('ENABLE_ACTUATION','false').lower()=='true'
    config['secure_cookie']=os.getenv('BALANCE_COOKIE_SECURE','false').lower()=='true'
    return config

def state_path():return Path(os.getenv('BALANCE_STATE_DIR',str(ROOT/'.state'))).resolve()
