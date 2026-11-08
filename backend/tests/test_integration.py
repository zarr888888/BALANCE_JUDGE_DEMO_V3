import shutil
import copy
import json
from pathlib import Path
import pytest
from app import create_app

ROOT = Path(__file__).resolve().parents[1]
WEB = 'integrated-dashboard-test-key-1234567890'
DEVICE = 'integrated-device-test-key-123456789000'
CONFIG = {'dashboard_key': WEB, 'device_keys': {'esp32-demo': DEVICE}}


@pytest.fixture(scope='module')
def integrated(tmp_path_factory):
    state=tmp_path_factory.mktemp('integrated-state')
    shutil.copytree(ROOT/'.state',state,dirs_exist_ok=True)
    (state/'integration.sqlite3').unlink(missing_ok=True)
    app = create_app(state, CONFIG)
    app.testing = True
    return app


@pytest.fixture()
def browser(integrated):
    with integrated.extensions['balance'].store.connect() as db:
        db.execute('DELETE FROM records')
    return integrated.test_client()


def login(c):
    assert c.post('/api/session', json={'dashboard_key': WEB}).status_code == 200


def raw(**changes):
    return dict(device_id='esp32-demo', record_id='legacy-test', water_distance=12,
                flow_a=0, flow_b=1, flow_c=2, **changes)


def test_dashboard_cookie_hides_backend_key(browser):
    assert browser.get('/api/history').status_code == 401
    response = browser.post('/api/session', json={'dashboard_key': WEB})
    assert response.get_json() == {'authenticated': True}
    cookie = response.headers['Set-Cookie']
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie and WEB not in cookie
    assert browser.get('/api/history').status_code == 200
    browser.delete('/api/session')
    assert browser.get('/api/history').status_code == 401


@pytest.mark.parametrize('path', ['/api/plan', '/api/predict', '/api/optimize', '/api/iot/simulate'])
def test_protected_writes(browser, path):
    assert browser.post(path, json={}).status_code == 401
    assert browser.post(path, json={}, headers={'X-Balance-Key': DEVICE}).status_code == 401


def test_foreign_browser_origin_rejected(browser):
    login(browser)
    assert browser.post('/api/plan', json={}, headers={'Origin': 'http://evil.example'}).status_code == 403
    assert browser.post('/api/plan', json={}, headers={'Origin': 'http://localhost'}).status_code == 200


def test_invalid_unicode_key_is_not_server_error(browser):
    assert browser.post('/api/session', json={'dashboard_key': 'salah-🔑'}).status_code == 401


def test_empty_install_does_not_invent_history_or_zones(browser):
    login(browser)
    assert browser.get('/api/zones').get_json() == []
    assert browser.get('/api/history').get_json() == []
    assert browser.get('/api/plan/latest').status_code == 404
    assert browser.get('/api/schedule').status_code == 404


def test_ui_endpoints_share_one_real_plan_snapshot(browser):
    login(browser)
    result = browser.post('/api/plan', json={'request_id': 'ui-plan'}).get_json()
    assert result['predictions'][0]['selected_ml_method'] in ('gradient_boosting', 'ridge')
    zones = browser.get('/api/zones').get_json()
    assert len(zones) == 3 and all(z['unit'] == 'mL demo' for z in zones)
    assert sum(z['release_ml'] for z in zones) == pytest.approx(result['source_budget']['available_demo_ml'], abs=1e-6)
    assert browser.get('/api/schedule').get_json()['schedule'] == result['schedule']
    assert browser.get('/api/plan/latest').get_json()['plan_id'] == result['plan_id']
    assert browser.get('/api/history').get_json()[0]['plan_id'] == result['plan_id']
    assert result['execution_allowed'] is False


def test_baseline_and_assumed_eta_controls_are_used(browser):
    login(browser)
    r = browser.post('/api/plan', json={'prediction_mode': 'baseline', 'loss_mode': 'assumed'})
    assert r.status_code == 200
    p = r.get_json()
    assert p['eta']['inference_used'] is False
    assert next(z for z in p['eta']['zones'] if z['zone_id'] == 'C')['delivery_efficiency'] == 0.8
    assert p['predictions'][0]['method_used'] == 'agronomic_forecast'


def test_custom_form_features_change_actual_predictions(browser):
    login(browser)
    scenario = browser.get('/api/scenario').get_json()
    f = scenario['zones'][0]['features']
    f.update(area_m2=5000, kc_assumed=1, et0_forecast_mm=4, effective_rain_forecast_mm=1,
        percolation_assumed_mm=4, etc_forecast_L=20000, percolation_assumed_L=20000, effective_rain_forecast_L=5000)
    r = browser.post('/api/plan', json={'prediction_mode':'baseline', 'feature_scenario':scenario})
    assert r.status_code == 200
    assert next(p for p in r.get_json()['predictions'] if p['zone_id']=='A')['value_used_L'] == 35000


def test_failed_plan_does_not_write_history(browser):
    login(browser)
    assert browser.post('/api/plan', json={'horizon_s':1}).status_code == 422
    assert browser.get('/api/history').get_json() == []


def test_zero_budget_empty_schedule(browser):
    login(browser)
    r = browser.post('/api/plan', json={'supply_fraction':0}).get_json()
    assert r['source_budget']['available_demo_ml'] == 0
    assert r['schedule']['slots'] == [] and r['allocation']['status'] == 'NO_SUPPLY'


def test_no_demand_scenario_does_not_invent_fulfillment_or_break_history(browser):
    login(browser)
    scenario=browser.get('/api/scenario').get_json()
    for z in scenario['zones']:
        f=z['features'];f['effective_rain_forecast_mm']=200
        f['effective_rain_forecast_L']=f['area_m2']*200
    r=browser.post('/api/plan',json={'prediction_mode':'baseline','feature_scenario':scenario})
    assert r.status_code==200,r.get_json()
    assert all(z['need_ml']==0 and z['fulfillment_ratio'] is None for z in r.get_json()['allocation']['zones'])
    assert r.get_json()['schedule']['slots']==[]
    h=browser.get('/api/history')
    assert h.status_code==200 and h.get_json()[0]['disparity_pp'] is None


@pytest.mark.parametrize('raw_json', ['null', '[]', '{"supply_fraction":NaN}', '{"supply_fraction":0,"supply_fraction":1}', '{bad'])
def test_json_is_not_silently_coerced(browser, raw_json):
    login(browser)
    assert browser.post('/api/plan', data=raw_json, content_type='application/json').status_code == 422


def test_legacy_telemetry_is_raw_and_never_invented_volume(browser):
    r = browser.post('/api/iot/data', json=raw(), headers={'X-Balance-Key':DEVICE})
    assert r.status_code == 201
    assert r.get_json()['data']['flow_a'] == 0
    login(browser)
    saved = browser.get('/api/iot/data').get_json()['data'][0]
    assert saved['units'] == 'UNCONFIRMED' and saved['context'] == 'UNKNOWN'
    assert saved['stale'] is None and saved['observed_at_utc'] is None
    assert 'water_volume' not in saved and 'source_volume_ml' not in saved
    assert not saved['used_for_reoptimization'] and not saved['used_for_training']


def test_legacy_retry_and_conflict(browser):
    h={'X-Balance-Key':DEVICE}
    assert browser.post('/api/iot/data', json=raw(), headers=h).status_code==201
    assert browser.post('/api/iot/data', json=raw(), headers=h).status_code==200
    b=raw();b['flow_a']=1
    assert browser.post('/api/iot/data', json=b, headers=h).status_code==409


@pytest.mark.parametrize('bad', [True, -1, '1', None])
def test_legacy_numeric_validation(browser, bad):
    body=raw();body['flow_a']=bad
    assert browser.post('/api/iot/data', json=body, headers={'X-Balance-Key':DEVICE}).status_code==422


def test_legacy_identity_role_and_origin_labels_cannot_be_forged(browser):
    assert browser.post('/api/iot/data', json=raw()).status_code==401
    assert browser.post('/api/iot/data', json=raw(), headers={'X-Balance-Key':WEB}).status_code==401
    body=raw();body['device_id']='other'
    assert browser.post('/api/iot/data', json=body, headers={'X-Balance-Key':DEVICE}).status_code==403
    body=raw();body['context']='MINIATUR'
    assert browser.post('/api/iot/data', json=body, headers={'X-Balance-Key':DEVICE}).status_code==422


def test_canonical_telemetry_appears_in_dashboard_and_survives_restart(browser):
    from datetime import datetime, timezone
    b=dict(record_id='canonical-ui',device_id='esp32-demo',observed_at=datetime.now(timezone.utc).isoformat(),
        boot_id='test',context='MINIATUR',value_kind='MEASUREMENT',quality='UNCALIBRATED',zone_id='C',
        counter_scope='UNKNOWN',readings={'received_volume_ml':0,'flow_ml_s':6})
    assert browser.post('/api/balance/v1/telemetry',json=b,headers={'X-Balance-Key':DEVICE}).status_code==201
    login(browser)
    row=browser.get('/api/iot/data').get_json()['data'][0]
    assert row['context']=='MINIATUR' and row['stale'] is False
    other=create_app(browser.application.extensions['balance'].state,CONFIG).test_client()
    login(other)
    assert other.get('/api/iot/data').get_json()['data'][0]['record_id']=='canonical-ui'
    assert other.get('/api/status').get_json()['real_measurement_rows_in_training']==0


def test_simulation_button_cannot_create_measurement_or_new_plan(browser):
    login(browser)
    assert browser.post('/api/iot/simulate',json={}).status_code==200
    rows=browser.get('/api/iot/data').get_json()['data']
    assert len(rows)==3 and all(r['context']=='SIMULASI' and r['value_kind']=='SIMULATED' for r in rows)
    assert browser.get('/api/history').get_json()==[]


def test_no_legacy_unit_edit_or_actuation_endpoint(browser):
    login(browser)
    assert browser.put('/api/zones/A',json={'water_supply':100}).status_code==410
    assert browser.post('/api/actuate',json={'open':True}).status_code==404
    assert browser.get('/api/balance/v1/device/next-command',headers={'X-Balance-Key':DEVICE}).get_json()['command'] is None


@pytest.mark.parametrize('factor,child', [(0.8,True),(0.9,False),(1,False)])
def test_integrated_replay_threshold(browser,factor,child):
    login(browser)
    r=browser.post('/api/scheduler/simulate',json={'received_factor':factor})
    assert r.status_code==200
    assert r.get_json()['child_plan_created'] is child
    assert r.get_json()['ledger']['source_balance_ok'] is True
