"""Run while app.py is listening; sends simulated telemetry only."""
import argparse,json,uuid
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import Request,urlopen

ROOT=Path(__file__).resolve().parents[1]
def main(base):
    cfg=json.loads((ROOT/'config.local.json').read_text())
    def call(path,key=None,body=None):
        data=None if body is None else json.dumps(body,allow_nan=False).encode()
        req=Request(base+'/api/balance/v1'+path,data=data,headers={'Content-Type':'application/json',**({'X-Balance-Key':key} if key else {})})
        with urlopen(req,timeout=45) as r:return json.load(r)
    assert call('/health')['execution_allowed'] is False
    plan=call('/plan',cfg['dashboard_key'],{'request_id':'smoke-'+uuid.uuid4().hex,'context':'SIMULASI'})
    assert len(plan['allocation']['zones'])==3
    packet={'record_id':'smoke-'+uuid.uuid4().hex,'device_id':'esp32-demo','boot_id':'smoke-python',
      'observed_at':datetime.now(timezone.utc).isoformat(),'context':'SIMULASI','value_kind':'SIMULATED',
      'quality':'UNCALIBRATED','zone_id':'C','counter_scope':'UNKNOWN','readings':{'flow_ml_s':6.,'head_cm':20.,'received_volume_ml':12.}}
    stored=call('/telemetry',cfg['device_keys']['esp32-demo'],packet)
    assert stored['status']=='STORED'
    latest=call('/telemetry/latest?device_id=esp32-demo',cfg['dashboard_key'])
    assert latest['record_id']==packet['record_id']
    command=call('/device/next-command',cfg['device_keys']['esp32-demo'])
    assert command['command'] is None and command['execution_allowed'] is False
    print('HTTP_SMOKE_PASS: ML -> LP -> schedule; simulated telemetry -> API -> stored; no actuation.')
    print('Plan:',plan['plan_id'],'; budget:',round(plan['source_budget']['available_demo_ml'],2),'mL')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--base-url',default='http://127.0.0.1:5000')
    main(p.parse_args().base_url.rstrip('/'))
