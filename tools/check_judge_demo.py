"""HTTP acceptance check for the public judge demo. Never sends device commands."""
import argparse
import getpass
import http.cookiejar
import json
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
import uuid


def run_checks(base, username, password):
    parsed = urllib.parse.urlsplit(base)
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('', '/'):
        raise ValueError('Gunakan alamat dasar hosting tanpa path, query, atau kredensial.')
    if parsed.scheme != 'https' and not (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')):
        raise ValueError('Hosting publik harus memakai HTTPS.')
    base = base.rstrip('/')
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(path, body=None, method=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, method=method, headers={'Content-Type': 'application/json'})
        try:
            with opener.open(req, timeout=45) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as exc:
            return exc.code, json.load(exc)

    status, health = call('/api/balance/v1/health')
    assert status == 200 and health['status'] == 'READY', 'Layanan belum READY.'
    status, _ = call('/api/session', {'username': username, 'password': password})
    assert status == 200, 'Login juri gagal. Periksa akun; password tidak dicetak.'
    status, auth = call('/api/session')
    assert status == 200 and auth.get('role') == 'judge', 'Akun bukan role juri.'
    status, _ = call('/api/iot/v2/devices')
    assert status == 403, 'Pembatasan akses operator perlu ditinjau.'
    body = json.loads((Path(__file__).resolve().parents[1] / 'backend/examples/plan_from_features.json').read_text())
    body['request_id'] = 'judge-check-' + uuid.uuid4().hex
    status, plan = call('/api/plan', body)
    assert status == 200 and plan['context'] == 'SIMULASI', 'Rencana simulasi gagal.'
    assert plan['allocation']['total_release_ml'] <= plan['source_budget']['available_demo_ml'] + 1e-6, 'Neraca sumber tidak sesuai.'
    status, replay = call('/api/scheduler/simulate', {'received_factor': .8, 'supply_fraction': 1, 'horizon_s': 600})
    assert status == 200 and 'child_plan_created' in replay, 'Replay gagal.'
    status, _ = call('/api/iot/simulate', {})
    assert status == 200, 'Telemetri simulasi gagal.'
    status, telemetry = call('/api/iot/data')
    assert status == 200 and any(row.get('context') == 'SIMULASI' for row in telemetry['data']), 'Telemetri simulasi belum tersimpan.'
    status, _ = call('/api/session', method='DELETE')
    assert status == 200, 'Logout gagal.'
    status, _ = call('/api/history')
    assert status == 401, 'Sesi belum ditutup.'
    return {'status': 'JUDGE_HTTP_PASS', 'plan_id': plan['plan_id'], 'physical_actuation_tested': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base_url')
    args = parser.parse_args()
    try:
        result = run_checks(args.base_url, input('Username juri: ').strip(), getpass.getpass('Password juri (tidak ditampilkan): '))
        print(result['status'] + ': login -> ML/LP -> jadwal/replay -> telemetri SIMULASI -> logout; akses operator ditolak.')
        print('Tidak menguji sensor atau aktuasi fisik.')
    except (AssertionError, ValueError, OSError) as exc:
        raise SystemExit('JUDGE_CHECK_FAILED: ' + str(exc))
