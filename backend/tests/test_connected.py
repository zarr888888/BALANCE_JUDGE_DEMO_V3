import copy, json, shutil
from datetime import datetime,timedelta,timezone
from pathlib import Path
import pytest
from werkzeug.security import generate_password_hash
from app import create_app
ROOT=Path(__file__).resolve().parents[1]
WEB='operator-key-for-tests-123456789';DEV='esp32-key-for-tests-12345678900'
CONFIG={'dashboard_key':WEB,'device_keys':{'esp32-demo':DEV},'judge_username':'juri','judge_password_hash':generate_password_hash('judge-example-password'),'enable_actuation':True}

@pytest.fixture(scope='module')
def app(tmp_path_factory):
    state=tmp_path_factory.mktemp('command-state');shutil.copytree(ROOT/'.state',state,dirs_exist_ok=True);(state/'integration.sqlite3').unlink(missing_ok=True)
    a=create_app(state,CONFIG);a.testing=True;return a
@pytest.fixture()
def c(app):
    with app.extensions['balance'].store.connect() as db:
        for table in ['iot_events','iot_commands','iot_devices','records']:db.execute('DELETE FROM '+table)
    return app.test_client()
def web(c,path,b=None):return c.get(path,headers={'X-Balance-Key':WEB}) if b is None else c.post(path,json=b,headers={'X-Balance-Key':WEB})
def device(c,path,b=None,key=DEV):return c.get('/api/iot/v2/device'+path,headers={'X-Balance-Key':key}) if b is None else c.post('/api/iot/v2/device'+path,json=b,headers={'X-Balance-Key':key})
def hello(c,boot='boot-one',armed=True,ref='bench-test-declared'):
    r=device(c,'/hello',{'device_id':'esp32-demo','boot_id':boot,'firmware_version':'balance-iot/2.0','armed':armed,'pulses_per_liter':5880.,'calibration_ref':ref,'source_distance_cm':3.0});assert r.status_code==200,r.get_json()
def command(c,mode='DRY_RUN',plan=None):
    hello(c)
    p=plan or web(c,'/api/plan',{}).get_json()
    r=web(c,'/api/iot/v2/commands',{'plan_id':p['plan_id'],'device_id':'esp32-demo','mode':mode});assert r.status_code==200,r.get_json();return p,r.get_json()
def claim(c,cmd):
    r=device(c,'/commands/'+cmd['command_id']+'/claim',{'boot_id':'boot-one'});assert r.status_code==200,r.get_json();return r.get_json()
def event(c,cmd,typ,seq=1,values=None,**changes):
    b={'event_id':'event-'+str(seq),'boot_id':'boot-one','lease_token':cmd['lease_token'],'type':typ,'released_ml':values or dict(A=0,B=0,C=0)};b.update(changes)
    return device(c,'/commands/'+cmd['command_id']+'/events',b)

def test_judge_login_can_plan_but_never_queue_or_cancel(c):
    assert c.post('/api/session',json={'username':'juri','password':'judge-example-password'}).status_code==200
    assert c.get('/api/session').get_json()['role']=='judge'
    assert c.post('/api/plan',json={}).status_code==200
    assert c.post('/api/iot/v2/commands',json={}).status_code==403
    assert c.post('/api/iot/v2/commands/any/cancel',json={}).status_code==403
    assert c.get('/api/iot/v2/devices').status_code==403

def test_old_unversioned_quota_does_not_trigger_original_firmware(c):
    assert web(c,'/api/get_kuota').status_code==410
    assert c.get('/api/get_kuota').status_code==401

def test_one_shared_source_uses_release_not_received_and_keeps_schedule_order(c):
    p,cmd=command(c)
    assert [s['zone_id'] for s in cmd['slots']]==[s['zone_id'] for s in p['schedule']['slots']]
    assert sum(s['target_release_ml'] for s in cmd['slots'])==pytest.approx(p['allocation']['total_release_ml'])
    assert cmd['slots'][0]['target_release_ml']!=p['allocation']['zones'][2]['expected_delivered_ml']
    assert cmd['unit']=='mL' and cmd['measurement_location']=='SOURCE'

def test_command_retries_and_completed_plan_are_not_reissued(c):
    p,cmd=command(c);claimed=claim(c,cmd)
    assert claim(c,cmd)['lease_token']==claimed['lease_token']
    assert event(c,claimed,'STARTED').status_code==200
    values={z:next((s['target_release_ml'] for s in cmd['slots'] if s['zone_id']==z),0) for z in 'ABC'}
    assert event(c,claimed,'COMPLETED',2,values).status_code==200
    again=web(c,'/api/iot/v2/commands',{'plan_id':p['plan_id'],'device_id':'esp32-demo','mode':'DRY_RUN'}).get_json()
    assert again['command_id']==cmd['command_id'] and again['status']=='COMPLETED'
    assert device(c,'/next-command?boot_id=boot-one').get_json()['command'] is None
    assert device(c,'/commands/'+cmd['command_id']+'/claim',{'boot_id':'boot-one'}).status_code==409

def test_single_active_command_and_conflicting_mode(c):
    p,cmd=command(c)
    assert web(c,'/api/iot/v2/commands',{'plan_id':p['plan_id'],'device_id':'esp32-demo','mode':'MINIATUR'}).status_code==409
    p2=web(c,'/api/plan',{}).get_json()
    assert web(c,'/api/iot/v2/commands',{'plan_id':p2['plan_id'],'device_id':'esp32-demo','mode':'DRY_RUN'}).status_code==409

def test_reboot_does_not_resume_accepted_or_running_command(c):
    _,cmd=command(c);cl=claim(c,cmd);event(c,cl,'STARTED')
    hello(c,boot='new-boot',armed=False)
    assert web(c,'/api/iot/v2/commands').get_json()['commands'][0]['status']=='FAILED_REBOOT'
    assert device(c,'/next-command?boot_id=new-boot').get_json()['command'] is None

def test_claim_lease_and_monotonic_counters(c):
    _,cmd=command(c);cl=claim(c,cmd)
    assert event(c,cl,'STARTED',lease_token='wrong').status_code==403
    assert event(c,cl,'STARTED').status_code==200
    assert event(c,cl,'PROGRESS',2,dict(A=10,B=0,C=0)).status_code==200
    assert event(c,cl,'PROGRESS',3,dict(A=5,B=0,C=0)).status_code==422
    assert event(c,cl,'COMPLETED',4).status_code==422

def test_event_retry_and_conflict(c):
    _,cmd=command(c);cl=claim(c,cmd)
    assert event(c,cl,'STARTED').status_code==200
    assert event(c,cl,'STARTED').status_code==200
    assert event(c,cl,'FAILED',1).status_code==409

@pytest.mark.parametrize('value',[True,-1,float('inf'),'6'])
def test_bad_counters_rejected(c,value):
    _,cmd=command(c);cl=claim(c,cmd);event(c,cl,'STARTED')
    assert event(c,cl,'PROGRESS',2,dict(A=value,B=0,C=0)).status_code==422

def test_expiry_cancel_and_no_lease_exposure(c,app):
    _,cmd=command(c)
    with app.extensions['balance'].store.connect() as db:db.execute('UPDATE iot_commands SET expires_at=?',((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
    assert device(c,'/next-command?boot_id=boot-one').get_json()['command'] is None
    assert web(c,'/api/iot/v2/commands').get_json()['commands'][0]['status']=='EXPIRED'
    _,cmd2=command(c);cl=claim(c,cmd2)
    result=web(c,'/api/iot/v2/commands/'+cmd2['command_id']+'/cancel',{}).get_json()
    assert result['status']=='CANCEL_REQUESTED' and 'lease_token' not in result
    assert event(c,cl,'CANCELLED').get_json()['status']=='CANCELLED'

def test_arming_calibration_and_enabled_flag_are_required(c,app):
    p=web(c,'/api/plan',{}).get_json();b={'plan_id':p['plan_id'],'device_id':'esp32-demo','mode':'MINIATUR'}
    hello(c,armed=False);assert web(c,'/api/iot/v2/commands',b).status_code==422
    hello(c,ref='');assert web(c,'/api/iot/v2/commands',b).status_code==422
    hello(c);app.extensions['broker'].enabled=False
    try:assert web(c,'/api/iot/v2/commands',b).status_code==422
    finally:app.extensions['broker'].enabled=True
    assert web(c,'/api/iot/v2/commands',b).status_code==200

def test_dry_run_records_do_not_change_training_or_actual_delivery(c):
    p,cmd=command(c);cl=claim(c,cmd);event(c,cl,'STARTED')
    assert p['actual_measured'] is None and p['field_validated'] is False
    assert c.get('/api/status').get_json()['real_measurement_rows_in_training']==0

def test_command_journal_survives_server_restart(c,app):
    _,cmd=command(c);claim(c,cmd)
    other=create_app(app.extensions['balance'].state,CONFIG).test_client()
    assert web(other,'/api/iot/v2/commands').get_json()['commands'][0]['status']=='ACCEPTED'

def test_source_telemetry_never_becomes_received_volume(c):
    b=dict(record_id='source-test',device_id='esp32-demo',boot_id='boot',observed_at=datetime.now(timezone.utc).isoformat(),context='MINIATUR',value_kind='MEASUREMENT',quality='UNCALIBRATED',zone_id='SOURCE',counter_scope='SINCE_BOOT',readings={'released_volume_ml':42,'source_distance_cm':3})
    assert c.post('/api/balance/v1/telemetry',json=b,headers={'X-Balance-Key':DEV}).status_code==201
    rows=web(c,'/api/iot/data').get_json()['data'];assert rows[0]['readings']['released_volume_ml']==42
    assert 'received_volume_ml' not in rows[0]['readings']
