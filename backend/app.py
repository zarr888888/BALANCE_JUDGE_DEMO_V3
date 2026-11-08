"""BALANCE integrated prototype: ML -> LP -> scheduler, persistent telemetry.

Default storage is local SQLite. PostgreSQL in the original upload is not modified.
Versioned miniature commands are opt-in and separate from simulation planning.
"""
import argparse
import hashlib
import json
import math
import secrets
import sys
import uuid
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_from_directory, session
from balance_handoff import create_blueprint, create_service
from balance_handoff.service import Conflict, ReviewRequired, dumps
from runtime_config import settings, state_path
from werkzeug.security import check_password_hash

ROOT = Path(__file__).resolve().parent


def utcnow():
    return datetime.now(timezone.utc)


def create_app(state_dir=None, config=None):
    config = config or settings()
    service = create_service(state_dir or state_path())
    app = Flask(__name__, static_folder=None)
    app.config.update(SECRET_KEY=config['dashboard_key'], MAX_CONTENT_LENGTH=65536,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Strict',
                      SESSION_COOKIE_SECURE=config.get('secure_cookie',False), PERMANENT_SESSION_LIFETIME=timedelta(hours=2))
    app.extensions['balance'] = service
    app.register_blueprint(create_blueprint(service, dashboard_key=config['dashboard_key'],
        device_keys=config['device_keys'], url_prefix='/api/balance/v1'))

    def role():
        key = request.headers.get('X-Balance-Key', '')
        if secrets.compare_digest(key.encode(), config['dashboard_key'].encode()):return 'operator'
        return session.get('role','operator') if session.get('dashboard') is True else None

    def dashboard():return role() in ('operator','judge')

    def read_json():
        if not request.is_json:
            raise ValueError('Kirim Content-Type: application/json.')
        def pairs(values):
            result = {}
            for k, v in values:
                if k in result:
                    raise ValueError('Kunci JSON ganda.')
                result[k] = v
            return result
        def constant(_):
            raise ValueError('Angka JSON harus finite.')
        body = json.loads(request.get_data(), object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(body, dict):
            raise ValueError('Body harus objek JSON.')
        return body

    @app.before_request
    def access():
        # Browser uses the same origin (Vite proxy or built frontend). Device uses key.
        origin = request.headers.get('Origin')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin:
            parsed = urlsplit(origin)
            if parsed.scheme not in ('http', 'https') or parsed.netloc != request.host:
                return jsonify(error='ORIGIN_REJECTED'), 403
        if not request.path.startswith('/api/') or request.path.startswith('/api/balance/v1/'):
            return None
        public = ('/api/status', '/api/session')
        if request.path in public or request.path.startswith('/api/iot/v2/device/') or (request.path == '/api/iot/data' and request.method == 'POST'):
            return None
        if not dashboard():
            return jsonify(error='LOGIN_REQUIRED', message='Masukkan dashboard_key dari config.local.json.'), 401

    @app.after_request
    def headers(response):
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error='INVALID_INPUT', message=str(exc)[:300], execution_allowed=False), 422

    @app.errorhandler(Conflict)
    def conflict(exc):
        return jsonify(error='ID_CONFLICT', message=str(exc), execution_allowed=False), 409

    @app.errorhandler(ReviewRequired)
    def review(exc):
        return jsonify(error='REVIEW_REQUIRED', message=str(exc), execution_allowed=False), 422

    @app.errorhandler(413)
    def too_large(_):
        return jsonify(error='PAYLOAD_TOO_LARGE'), 413

    @app.errorhandler(500)
    def internal_error(_):
        return jsonify(error='REVIEW_REQUIRED_NO_NEW_RESULT', message='Perhitungan perlu ditinjau. Lihat terminal backend.', execution_allowed=False), 500

    failed_logins={}
    @app.route('/api/session', methods=['GET', 'POST', 'DELETE'])
    def login():
        if request.method == 'POST':
            body=read_json();address=request.remote_addr or 'unknown';now=time.monotonic()
            recent=[t for t in failed_logins.get(address,[]) if now-t<300]
            if len(recent)>=8:return jsonify(error='TOO_MANY_ATTEMPTS'),429
            key=body.get('dashboard_key');who=None
            if isinstance(key,str) and secrets.compare_digest(key.encode(), config['dashboard_key'].encode()):who='operator'
            elif isinstance(body.get('username'),str) and isinstance(body.get('password'),str) and len(body['password'])<=256 and config.get('judge_password_hash'):
                if secrets.compare_digest(body['username'].encode(),config.get('judge_username','juri').encode()) and check_password_hash(config['judge_password_hash'],body['password']):who='judge'
            if who is None:
                if len(failed_logins)>2048:failed_logins.clear()
                failed_logins[address]=recent+[now]
                return jsonify(error='KEY_INVALID'), 401
            failed_logins.pop(address,None)
            session.clear()
            session['dashboard'] = True
            session['role']=who;session.permanent=True
        elif request.method == 'DELETE':
            session.clear()
        return jsonify(authenticated=dashboard()) if request.method!='GET' else jsonify(authenticated=dashboard(),role=role())

    def records(kinds, limit=50):
        with service.store.connect() as db:
            rows = db.execute('SELECT body FROM records WHERE ' + kinds + ' ORDER BY seq DESC LIMIT ?', (limit,)).fetchall()
        return [json.loads(row[0]) for row in rows]

    @app.get('/api/status')
    def status():
        health = service.health()
        return jsonify(**health, backend='online', database='SQLite lokal',
            ai='Gradient Boosting / Ridge + LP', iot='Telemetri dan perintah miniatur berversi',
            integration_version='2.0', unit='mL demo', source_budget_scope='ONE_SHARED_SOURCE',
            miniature_commands_available=True,miniature_execution_enabled=config.get('enable_actuation',False))

    @app.post('/api/plan')
    @app.post('/api/predict')
    @app.post('/api/optimize')
    def plan():
        return jsonify(service.plan(read_json()))

    @app.get('/api/plan/latest')
    def latest():
        result = service.store.latest('plan')
        return jsonify(result) if result else (jsonify(error='NO_PLAN_YET'), 404)

    @app.get('/api/scenario')
    def scenario():
        return jsonify(json.loads((ROOT / 'examples' / 'plan_from_features.json').read_text())['feature_scenario'])

    @app.get('/api/zones')
    def zones():
        p = service.store.latest('plan')
        return jsonify([] if p is None else [dict(z, unit='mL demo') for z in p['allocation']['zones']])

    @app.put('/api/zones/<zone>')
    def retired_edit(zone):
        return jsonify(error='EDIT_SCENARIO_IN_ANALYTICS', message='Ubah fitur skenario di Analitik AI lalu hitung ulang. Nilai m3/hari lama tidak dicampur dengan mL demo.'), 410

    @app.get('/api/history')
    def history():
        return jsonify([{'plan_id':p['plan_id'], 'created_at':p['created_at'], 'context':p['context'],
            'budget_ml':p['source_budget']['available_demo_ml'], 'release_ml':p['allocation']['total_release_ml'],
            'status':p['allocation']['status'], 'disparity_pp':None if p['allocation']['disparity_ratio'] is None else p['allocation']['disparity_ratio']*100,
            'execution_allowed':False} for p in records("kind='plan'")])

    @app.get('/api/schedule')
    def schedule():
        p = service.store.latest('plan')
        return jsonify(plan_id=p['plan_id'], schedule=p['schedule'], execution_allowed=False) if p else (jsonify(error='NO_PLAN_YET'), 404)

    @app.post('/api/scheduler/simulate')
    def scheduler():
        return jsonify(service.scheduler(read_json()))

    @app.post('/api/history/simulate')
    def history_sim():
        return jsonify(service.history(read_json()))

    def telemetry_list():
        rows = records("kind LIKE 'telemetry:%' OR kind LIKE 'legacy:%'")
        for r in rows:
            stamp = r.get('observed_at_utc')
            r['age_s'] = max(0, (utcnow()-datetime.fromisoformat(stamp)).total_seconds()) if stamp else None
            r['stale'] = r['age_s'] > 30 if stamp else None
        return rows

    @app.route('/api/iot/data', methods=['GET', 'POST'])
    def iot_data():
        if request.method == 'GET':
            return jsonify(success=True, data=telemetry_list(), execution_allowed=False)
        token = request.headers.get('X-Balance-Key', '')
        device = next((d for d, k in config['device_keys'].items() if secrets.compare_digest(token.encode(), k.encode())), None)
        if device is None:
            return jsonify(error='UNAUTHORIZED_DEVICE'), 401
        body = read_json()
        if body.get('device_id') != device:
            return jsonify(error='DEVICE_ID_MISMATCH'), 403
        required = {'device_id', 'water_distance', 'flow_a', 'flow_b', 'flow_c'}
        if set(body) - (required | {'record_id'}) or not required.issubset(body):
            raise ValueError('Payload format lama: device_id, water_distance, flow_a/b/c; opsional record_id.')
        for name in required - {'device_id'}:
            v = body[name]
            if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) or v < 0:
                raise ValueError(name + ' harus angka nonnegatif finite.')
        key = body.get('record_id', uuid.uuid4().hex)
        if not isinstance(key, str) or not 1 <= len(key) <= 100:
            raise ValueError('record_id tidak valid.')
        record = dict(body, record_id=key, received_at=utcnow().isoformat(), context='UNKNOWN',
            value_kind='UNCONFIRMED', quality='UNCALIBRATED', units='UNCONFIRMED',
            observed_at_utc=None, format='LEGACY_RAW', used_for_training=False,
            used_for_reoptimization=False, execution_allowed=False)
        saved, created = service.store.put('legacy:'+device, key, hashlib.sha256(dumps(body).encode()).hexdigest(), record)
        return jsonify(success=True, data=saved, warning='Satuan dan asal data belum dikonfirmasi. Nilai mentah hanya ditampilkan; tidak dipakai untuk optimasi.'), 201 if created else 200

    @app.post('/api/iot/simulate')
    def simulate_iot():
        read_json()
        # Explicit dashboard demonstration; never relabel as sensor measurement.
        for zone in 'ABC':
            service.record_telemetry(dict(record_id=uuid.uuid4().hex, device_id='esp32-demo',
                boot_id='dashboard-simulation', observed_at=utcnow().isoformat(), context='SIMULASI',
                value_kind='SIMULATED', quality='UNCALIBRATED', zone_id=zone, counter_scope='UNKNOWN',
                readings={'flow_ml_s':6., 'head_cm':20.}), 'esp32-demo')
        return jsonify(status='SIMULATED_TELEMETRY_STORED', execution_allowed=False)

    @app.get('/')
    def index():
        dist = ROOT.parent / 'frontend' / 'dist'
        if (dist/'index.html').exists():
            return send_from_directory(dist, 'index.html')
        return jsonify(status='BACKEND_READY', message='Jalankan frontend: npm run dev. Buka http://localhost:5173.')

    @app.route('/<path:path>', methods=['GET', 'POST', 'PUT', 'DELETE', 'PATCH'])
    def asset(path):
        if path.startswith('api/'):
            return jsonify(error='NOT_FOUND'), 404
        if request.method != 'GET':
            return jsonify(error='METHOD_NOT_ALLOWED'), 405
        return send_from_directory(ROOT.parent/'frontend'/'dist', path)

    @app.get('/api/get_kuota')
    def retired_quota():
        return jsonify(error='FIRMWARE_V2_REQUIRED',message='Gunakan perintah dengan command_id dan unit mL; endpoint lama tidak menggerakkan alat.'),410

    from connected import routes
    routes(app,service,config,read_json,role)

    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default=os.getenv('HOST','127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.getenv('PORT','5000')))
    args = parser.parse_args()
    try:
        app = create_app()
    except (ValueError, FileNotFoundError) as exc:
        print('Backend belum siap:', exc, '\nJalankan python setup_demo.py dari folder utama.')
        sys.exit(1)
    from waitress import serve
    print(f'BALANCE connected V2: http://{args.host}:{args.port}; model SIMULASI; MINIATUR enabled={app.extensions["broker"].enabled}', flush=True)
    serve(app, host=args.host, port=args.port, threads=4)
