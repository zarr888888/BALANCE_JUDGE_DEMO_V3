import copy, hashlib, json, math, sqlite3, threading, uuid
from datetime import datetime, timezone
from pathlib import Path
from jsonschema import Draft202012Validator, FormatChecker
from .engine import DemoEngine
from .vendor.balance_ml.core import FEATURES, finite_nonnegative, timestamp
from .vendor.balance_lp_v11 import Zone
from .vendor.balance_scheduler.core import Timing, build_schedule
from .vendor.balance_scheduler.replay import replay_from_demo
from .vendor.balance_history.replay import replay_history

PREFIX='balance-handoff/1.0'
ROOT=Path(__file__).resolve().parent
def dumps(obj):return json.dumps(obj,ensure_ascii=False,sort_keys=True,allow_nan=False)
def utcnow():return datetime.now(timezone.utc)
def finite_tree(value):
    if isinstance(value,float) and not math.isfinite(value):raise ValueError('Nonfinite value')
    if isinstance(value,dict):
        for v in value.values():finite_tree(v)
    if isinstance(value,list):
        for v in value:finite_tree(v)
def validate(value,name):
    finite_tree(value)
    schema=json.loads((ROOT/'contracts'/name).read_text())
    errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(value))
    if errors:raise ValueError(errors[0].message)

class Conflict(ValueError):pass
class ReviewRequired(RuntimeError):pass

class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS records (seq INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, record_key TEXT, digest TEXT, body TEXT, created_at TEXT, UNIQUE(kind,record_key))')
    def connect(self):return sqlite3.connect(self.path,timeout=10)
    def get(self,kind,key):
        with self.connect() as db:r=db.execute('SELECT digest,body FROM records WHERE kind=? AND record_key=?',(kind,key)).fetchone()
        return (r[0],json.loads(r[1])) if r else None
    def put(self,kind,key,digest,body):
        with self.connect() as db:
            try:db.execute('INSERT INTO records(kind,record_key,digest,body,created_at) VALUES(?,?,?,?,?)',(kind,key,digest,dumps(body),utcnow().isoformat()))
            except sqlite3.IntegrityError:
                existing=self.get(kind,key)
                if existing[0]!=digest:raise Conflict('Identifier already used with a different payload')
                return existing[1],False
        return body,True
    def latest(self,kind,observation=False):
        order="json_extract(body,'$.observed_at_utc') DESC, seq DESC" if observation else 'seq DESC'
        with self.connect() as db:r=db.execute('SELECT body FROM records WHERE kind=? ORDER BY '+order+' LIMIT 1',(kind,)).fetchone()
        return json.loads(r[0]) if r else None
    def count(self,kind):
        with self.connect() as db:return db.execute('SELECT COUNT(*) FROM records WHERE kind=?',(kind,)).fetchone()[0]

class BalanceService:
    def __init__(self,state_dir):
        self.state=Path(state_dir).resolve()
        try:manifest=json.loads((self.state/'active.json').read_text())
        except FileNotFoundError:raise ValueError('Models not prepared. Run: python prepare_models.py') from None
        if manifest.get('schema_version')!='balance-local-artifacts/1' or manifest.get('training_context')!='SIMULASI':raise ValueError('Unsupported artifact manifest')
        modelroot=(self.state/manifest['run_dir']).resolve()
        if not modelroot.is_relative_to(self.state):raise ValueError('Invalid artifact path')
        for name,digest in manifest['sha256'].items():
            p=(modelroot/name).resolve()
            if not p.is_relative_to(modelroot) or hashlib.sha256(p.read_bytes()).hexdigest()!=digest:raise ValueError('Model artifact integrity check failed')
        # Only locally prepared, hash-checked artifacts; no model upload endpoint.
        self.engine=DemoEngine(modelroot/'ml',modelroot/'loss')
        self.store=Store(self.state/'integration.sqlite3')
        self.lock=threading.RLock()
    def health(self):
        return {'schema_version':PREFIX,'status':'READY','planning_context':'SIMULASI',
          'ml_status':'TRAINED_SYNTHETIC_ONLY','real_measurement_rows_in_training':0,
          'field_validated':False,'execution_allowed':False,'actuation_endpoint_available':False,
          'telemetry_records_stored':sum(self.store.count('telemetry:'+d) for d in self.device_ids())}
    def device_ids(self):
        with self.store.connect() as db:return [r[0].split(':',1)[1] for r in db.execute("SELECT DISTINCT kind FROM records WHERE kind LIKE 'telemetry:%'")]
    def scenario_engine(self,scenario):
        e=copy.copy(self.engine)
        as_of=timestamp(scenario['as_of']);end=timestamp(scenario['target_end'])
        if (end-as_of).total_seconds()!=86400:raise ValueError('Prediction horizon must be 24 hours')
        available=timestamp(scenario['features_available_at'])
        if available>as_of:raise ValueError('Features must be available by as_of')
        rows=[]
        for task,zone,features in [('supply',None,scenario['supply'])]+[('demand',r['zone_id'],r['features']) for r in scenario['zones']]:
            if set(features)!=set(FEATURES[task]):raise ValueError('Feature names do not match the model')
            for key,v in features.items():finite_nonnegative(v,key)
            if task=='demand':
                f=features
                if f['area_m2']<=0:raise ValueError('Area must be positive')
                for k,v in {'etc_forecast_L':f['area_m2']*f['kc_assumed']*f['et0_forecast_mm'],
                  'percolation_assumed_L':f['area_m2']*f['percolation_assumed_mm'],
                  'effective_rain_forecast_L':f['area_m2']*f['effective_rain_forecast_mm']}.items():
                    if not math.isclose(f[k],v,rel_tol=1e-9,abs_tol=1e-8):raise ValueError('Derived demand feature inconsistent: '+k)
            rows.append({'task':task,'zone_id':zone,'features':features,'as_of':scenario['as_of'],'target_end':scenario['target_end']})
        if len({r['zone_id'] for r in rows if r['task']=='demand'})!=3:raise ValueError('Zones A/B/C must be unique')
        e.scenario=rows;e.cycle_id='api-synthetic-'+uuid.uuid4().hex[:16]
        return e
    def plan(self,body):
        validate(body,'plan_request.schema.json')
        key=body.get('request_id') or uuid.uuid4().hex
        digest=hashlib.sha256(dumps(body).encode()).hexdigest()
        with self.lock:
            old=self.store.get('plan',key)
            if old:
                if old[0]!=digest:raise Conflict('request_id has different content')
                return old[1]
            e=self.scenario_engine(body['feature_scenario']) if 'feature_scenario' in body else self.engine
            options={k:body.get(k,v) for k,v in {'prediction_mode':'ml','supply_fraction':1.,'tail_capacity_ml':5000.,'loss_mode':'learned','loss_flow_ml_s':6.,'loss_head_cm':20.}.items()}
            detail=e.calculate(options)
            horizon=body.get('horizon_s',600.)
            zones=[Zone(r['zone_id'],r['position'],r['need_ml'],r['delivery_efficiency'],r['max_release_ml']) for r in detail['allocation_inputs']]
            timings={z.zone_id:Timing(options['loss_flow_ml_s'],{'A':2.,'B':4.,'C':6.}[z.zone_id],1.,horizon) for z in zones}
            schedule=build_schedule(detail['allocation'],zones,timings,horizon_end_s=horizon)
            if schedule['status']=='REVIEW_UNSCHEDULABLE':raise ReviewRequired('Schedule does not fit horizon; no plan saved')
            response={'schema_version':PREFIX,'plan_id':'p_'+uuid.uuid4().hex,'request_id':key,
              'created_at':utcnow().isoformat(),'context':'SIMULASI','status':'RECOMMENDATION_FOR_REVIEW',
              'execution_allowed':False,'field_validated':False,'real_measurement_rows_in_training':0,
              'ml_status':'TRAINED_SYNTHETIC_ONLY','actual_measured':None,
              'feature_source':'CALLER_SYNTHETIC_SCENARIO' if 'feature_scenario' in body else 'FIRST_SAVED_SYNTHETIC_TEST_CYCLE',
              'predictions':detail['predictions'],'source_budget':detail['source_budget'],
              'eta':detail['loss_profile'],'allocation':detail['allocation'],'schedule':schedule,
              'comparison_baseline':detail['comparison_baseline'],'warnings':detail['warnings'],
              'scenario':detail['scenario'],'conversion':detail['conversion']}
            validate(response,'plan_response.schema.json')
            return self.store.put('plan',key,digest,response)[0]
    def scheduler(self,body):
        validate(body,'scheduler_request.schema.json')
        with self.lock:r=replay_from_demo(self.engine,**body)
        validate(r,'scheduler_response.schema.json');return r
    def history(self,body):
        validate(body,'history_request.schema.json')
        options=dict(body)
        if 'history_window_cycles' in options:options['history_window_cycles']=int(options['history_window_cycles'])
        with self.lock:r=replay_history(self.engine,**options)
        validate(r,'history_response.schema.json');return r
    def record_telemetry(self,body,device):
        validate(body,'telemetry.schema.json')
        if body['device_id']!=device:raise PermissionError('Device identity does not match key')
        if (body['context'],body['value_kind']) not in [('SIMULASI','SIMULATED'),('MINIATUR','MEASUREMENT')]:raise ValueError('Context/value_kind mismatch')
        if body['quality']=='VALID' and not body.get('calibration_ref'):raise ValueError('VALID requires a calibration reference')
        when=timestamp(body['observed_at'])
        if (when-utcnow()).total_seconds()>60:raise ValueError('Observation timestamp too far in future')
        digest=hashlib.sha256(dumps(body).encode()).hexdigest()
        record={**body,'observed_at_utc':when.astimezone(timezone.utc).isoformat(),'received_at':utcnow().isoformat(),'used_for_training':False,
                'used_for_reoptimization':False,'physical_measurement_authenticated':False,
                'execution_allowed':False}
        return self.store.put('telemetry:'+device,body['record_id'],digest,record)
    def latest_telemetry(self,device):
        record=self.store.latest('telemetry:'+device,observation=True)
        if record is None:return None
        age=max(0.,(utcnow()-timestamp(record['observed_at'])).total_seconds())
        return {**record,'age_s':age,'stale':age>30.,'stale_after_s':30.}
