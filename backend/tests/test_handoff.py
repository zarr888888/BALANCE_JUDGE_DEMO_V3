import copy,json,math,uuid,shutil
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from flask import Flask
from balance_handoff import create_service,create_blueprint
from balance_handoff.service import Conflict,ReviewRequired

ROOT=Path(__file__).resolve().parents[1]
WEB='dashboard-test-key-1234567890'
DEVICE='device-test-key-123456789000'

@pytest.fixture(scope='session')
def service(tmp_path_factory):
    state=tmp_path_factory.mktemp('core-state')
    shutil.copytree(ROOT/'.state',state,dirs_exist_ok=True)
    (state/'integration.sqlite3').unlink(missing_ok=True)
    return create_service(state)

@pytest.fixture(autouse=True)
def clean(service):
    with service.store.connect() as db:db.execute('DELETE FROM records')

@pytest.fixture()
def app(service):
    app=Flask(__name__)
    @app.get('/dashboard-existing')
    def original():return {'existing':'preserved'}
    app.register_blueprint(create_blueprint(service,dashboard_key=WEB,device_keys={'esp32-demo':DEVICE}))
    app.testing=True
    return app

@pytest.fixture()
def client(app):return app.test_client()

def post(client,path,body,key=WEB):return client.post('/api/balance/v1'+path,json=body,headers={'X-Balance-Key':key})

def packet(**changes):
    return {'record_id':uuid.uuid4().hex,'device_id':'esp32-demo','boot_id':'test-boot',
      'observed_at':datetime.now(timezone.utc).isoformat(),'context':'SIMULASI','value_kind':'SIMULATED',
      'quality':'UNCALIBRATED','zone_id':'C','counter_scope':'SINCE_BOOT',
      'readings':{'flow_ml_s':6.,'received_volume_ml':0.},**changes}

def test_health_does_not_claim_physical_validation(client):
    r=client.get('/api/balance/v1/health').get_json()
    assert r['ml_status']=='TRAINED_SYNTHETIC_ONLY'
    assert r['execution_allowed'] is False and r['field_validated'] is False
    assert r['real_measurement_rows_in_training']==0

def test_blueprint_preserves_existing_dashboard(client):
    assert client.get('/dashboard-existing').get_json()=={'existing':'preserved'}

@pytest.mark.parametrize('route',['/plan','/scheduler/simulate','/history/simulate','/telemetry'])
def test_write_endpoints_require_auth(client,route):assert post(client,route,{},key='wrong').status_code==401

def test_device_cannot_create_plan(client):assert post(client,'/plan',{},key=DEVICE).status_code==403
def test_dashboard_cannot_impersonate_sensor(client):assert post(client,'/telemetry',packet()).status_code==403

def test_plan_pipeline_preserves_source_budget_and_frozen_eta(client):
    response=post(client,'/plan',{'request_id':'pipeline-001'})
    assert response.status_code==200,response.get_json()
    r=response.get_json();zones=r['allocation']['zones']
    assert len(zones)==3 and len(r['predictions'])==4
    assert abs(sum(z['release_ml'] for z in zones)-r['allocation']['total_release_ml'])<1e-6
    assert r['allocation']['total_release_ml']<=r['source_budget']['available_demo_ml']+1e-6
    eta={x['zone_id']:x['delivery_efficiency'] for x in r['eta']['zones']}
    for z in zones:assert math.isclose(z['expected_delivered_ml'],z['release_ml']*eta[z['zone_id']],abs_tol=1e-6)
    slots=r['schedule']['slots']
    assert all(a['verification_end_s']<=b['start_s'] for a,b in zip(slots,slots[1:]))
    assert r['execution_allowed'] is False and r['actual_measured'] is None

def test_request_retry_is_idempotent(client,service):
    a=post(client,'/plan',{'request_id':'retry-001'}).get_json()
    b=post(client,'/plan',{'request_id':'retry-001'}).get_json()
    assert a==b and service.store.count('plan')==1

def test_request_id_reuse_with_different_payload_is_conflict(client):
    assert post(client,'/plan',{'request_id':'conflict-001'}).status_code==200
    assert post(client,'/plan',{'request_id':'conflict-001','supply_fraction':0.5}).status_code==409

def test_custom_features_are_used_and_baseline_is_explicit(client):
    body=json.loads((ROOT/'examples/plan_from_features.json').read_text())
    body['prediction_mode']='baseline'
    f=body['feature_scenario']['zones'][0]['features']
    f.update(area_m2=5000,kc_assumed=1.,et0_forecast_mm=4.,effective_rain_forecast_mm=1.,percolation_assumed_mm=4.,etc_forecast_L=20000.,percolation_assumed_L=20000.,effective_rain_forecast_L=5000.)
    r=post(client,'/plan',body)
    assert r.status_code==200,r.get_json()
    a=next(x for x in r.get_json()['predictions'] if x['task']=='demand' and x['zone_id']=='A')
    assert a['value_used_L']==35000 and a['method_used']=='agronomic_forecast'

@pytest.mark.parametrize('case',['future_features','duplicate_zone','inconsistent_volume','wrong_horizon','unknown_feature'])
def test_custom_feature_contract_rejects_leakage_and_mismatch(client,case):
    body=json.loads((ROOT/'examples/plan_from_features.json').read_text());s=body['feature_scenario']
    if case=='future_features':s['features_available_at']=s['target_end']
    if case=='duplicate_zone':s['zones'][1]['zone_id']='A'
    if case=='inconsistent_volume':s['zones'][0]['features']['etc_forecast_L']+=100
    if case=='wrong_horizon':s['target_end']=s['as_of']
    if case=='unknown_feature':s['supply']['label_future_actual']=100
    assert post(client,'/plan',body).status_code==422

@pytest.mark.parametrize('body',[None,[],{'supply_fraction':True},{'supply_fraction':-1},{'supply_fraction':1.1},{'context':'REAL'},{'execute':True},{'loss_head_cm':0}])
def test_invalid_plan_inputs(client,body):
    r=client.post('/api/balance/v1/plan',data=json.dumps(body),content_type='application/json',headers={'X-Balance-Key':WEB})
    assert r.status_code==422

@pytest.mark.parametrize('raw',['{"supply_fraction":NaN}','{"supply_fraction":0.2,"supply_fraction":0.3}','{bad'])
def test_strict_json_parser(client,raw):
    r=client.post('/api/balance/v1/plan',data=raw,content_type='application/json',headers={'X-Balance-Key':WEB})
    assert r.status_code==400

def test_numeric_overflow_is_rejected(client):
    r=client.post('/api/balance/v1/plan',data='{"supply_fraction":1e999}',content_type='application/json',headers={'X-Balance-Key':WEB})
    assert r.status_code==422

def test_payload_and_content_type_limits(client):
    assert client.post('/api/balance/v1/plan',data='{}',headers={'X-Balance-Key':WEB}).status_code==415
    assert client.post('/api/balance/v1/plan',data=' '*65537,content_type='application/json',headers={'X-Balance-Key':WEB}).status_code==413

def test_failed_schedule_does_not_save_a_new_plan(client,service):
    old=post(client,'/plan',{'request_id':'good'}).get_json()
    assert post(client,'/plan',{'request_id':'bad','horizon_s':1}).status_code==503
    assert service.store.count('plan')==1 and service.store.latest('plan')['plan_id']==old['plan_id']

def test_zero_supply_is_empty_distribution(client):
    r=post(client,'/plan',{'supply_fraction':0}).get_json()
    assert r['allocation']['status']=='NO_SUPPLY'
    assert r['source_budget']['available_demo_ml']==0 and r['schedule']['slots']==[]

def test_blocked_tail_is_reported(client):
    r=post(client,'/plan',{'tail_capacity_ml':0}).get_json()
    assert r['allocation']['status']=='DEGRADED_TAIL'
    tail=next(z for z in r['allocation']['zones'] if z['zone_id']=='C')
    assert tail['release_ml']==0 and tail['tail_guard']['status']=='UNMET'

@pytest.mark.parametrize('factor,expected',[(0.8,True),(0.9,False),(1.,False)])
def test_replay_trigger_boundary_and_budget(client,factor,expected):
    r=post(client,'/scheduler/simulate',{'received_factor':factor})
    assert r.status_code==200,r.get_json()
    result=r.get_json()
    assert result['child_plan_created'] is expected
    assert result['ledger']['source_balance_ok'] is True
    assert result['execution_allowed'] is False and result['actual_measured'] is None

def test_history_experiment_preserves_default_and_declares_limits(client):
    r=post(client,'/history/simulate',{})
    assert r.status_code==200,r.get_json()
    result=r.get_json()
    assert result['experiment']['default_demo13_policy_changed'] is False
    assert result['field_validated'] is False
    assert result['uniform_history_control']['zone_specific_effect_isolated_in_main_replay'] is False

def test_telemetry_retry_and_zero_are_preserved(client,service):
    body=packet();a=post(client,'/telemetry',body,key=DEVICE);b=post(client,'/telemetry',body,key=DEVICE)
    assert a.status_code==201 and b.status_code==200
    assert service.store.count('telemetry:esp32-demo')==1
    record=b.get_json()['record']
    assert record['readings']['received_volume_ml']==0
    assert record['used_for_training'] is False and record['used_for_reoptimization'] is False

def test_telemetry_conflicting_retry_is_rejected(client):
    body=packet();assert post(client,'/telemetry',body,key=DEVICE).status_code==201
    body['readings']['head_cm']=21
    assert post(client,'/telemetry',body,key=DEVICE).status_code==409

@pytest.mark.parametrize('change',[{'value_kind':'MEASUREMENT'},{'observed_at':'not-time'},{'quality':'VALID'},{'readings':{'flow_ml_s':True}},{'readings':{}},{'readings':{'flow_ml_s':-1}}])
def test_invalid_telemetry(client,change):assert post(client,'/telemetry',packet(**change),key=DEVICE).status_code==422

def test_device_id_must_match_its_key(client):assert post(client,'/telemetry',packet(device_id='other-device'),key=DEVICE).status_code==403

def test_future_timestamp_rejected(client):
    body=packet(observed_at=(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat())
    assert post(client,'/telemetry',body,key=DEVICE).status_code==422

def test_latest_uses_observation_time_not_arrival_and_marks_stale(client,service):
    fresh=packet();old=packet(observed_at=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat())
    assert post(client,'/telemetry',fresh,key=DEVICE).status_code==201
    assert post(client,'/telemetry',old,key=DEVICE).status_code==201
    r=service.latest_telemetry('esp32-demo')
    assert r['record_id']==fresh['record_id'] and r['stale'] is False
    with service.store.connect() as db:db.execute('DELETE FROM records')
    post(client,'/telemetry',old,key=DEVICE)
    assert service.latest_telemetry('esp32-demo')['stale'] is True

def test_miniature_packet_does_not_relabel_training_or_authenticate_measurement(client,service):
    r=post(client,'/telemetry',packet(context='MINIATUR',value_kind='MEASUREMENT',quality='VALID',calibration_ref='declared-test-calibration'),key=DEVICE)
    assert r.status_code==201
    assert r.get_json()['record']['physical_measurement_authenticated'] is False
    assert service.health()['real_measurement_rows_in_training']==0

def test_persistence_survives_service_restart(client,service):
    body=packet();post(client,'/telemetry',body,key=DEVICE)
    restarted=create_service(service.state)
    assert restarted.latest_telemetry('esp32-demo')['record_id']==body['record_id']

def test_device_cannot_read_another_devices_telemetry(client):
    r=client.get('/api/balance/v1/telemetry/latest?device_id=other-device',headers={'X-Balance-Key':DEVICE})
    assert r.status_code==403

def test_no_actuator_command_can_be_issued(client):
    r=client.get('/api/balance/v1/device/next-command',headers={'X-Balance-Key':DEVICE}).get_json()
    assert r['command'] is None and r['execution_allowed'] is False

def test_artifact_integrity_fails_before_model_load(tmp_path):
    run=tmp_path/'models'/'fake';run.mkdir(parents=True)
    (run/'test.bin').write_bytes(b'not-a-model')
    (tmp_path/'active.json').write_text(json.dumps({'schema_version':'balance-local-artifacts/1','training_context':'SIMULASI','run_dir':'models/fake','sha256':{'test.bin':'0'*64}}))
    with pytest.raises(ValueError,match='integrity'):create_service(tmp_path)

def test_short_or_reused_keys_rejected(service):
    with pytest.raises(ValueError):create_blueprint(service,dashboard_key='short')
    with pytest.raises(ValueError):create_blueprint(service,dashboard_key=WEB,device_keys={'device':WEB})

def test_non_ascii_header_is_unauthorized_without_crashing(client):assert post(client,'/plan',{},key='é'*30).status_code==401
