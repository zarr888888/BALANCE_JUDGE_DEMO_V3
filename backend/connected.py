"""Versioned, persistent miniature commands. No automatic replay of accepted jobs."""
import hashlib
import json
import math
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request
from balance_handoff.service import Conflict, dumps

TERMINAL={'COMPLETED','FAILED','CANCELLED','EXPIRED','FAILED_REBOOT','FAILED_TIMEOUT'}
NOW=lambda:datetime.now(timezone.utc)

def number(value,name,maximum=1000000):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=maximum:
        raise ValueError(name+' harus angka finite dalam batas yang diizinkan.')
    return float(value)

class Broker:
    def __init__(self,service,enabled=False):
        self.service=service;self.store=service.store;self.enabled=enabled
        with self.store.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS iot_devices(device_id TEXT PRIMARY KEY,boot_id TEXT,body TEXT,last_seen TEXT);
            CREATE TABLE IF NOT EXISTS iot_commands(command_id TEXT PRIMARY KEY,device_id TEXT,plan_id TEXT,status TEXT,body TEXT,
              created_at TEXT,expires_at TEXT,boot_id TEXT,lease_token TEXT,deadline TEXT,UNIQUE(device_id,plan_id));
            CREATE UNIQUE INDEX IF NOT EXISTS iot_one_active ON iot_commands(device_id) WHERE status IN ('QUEUED','ACCEPTED','RUNNING','CANCEL_REQUESTED');
            CREATE TABLE IF NOT EXISTS iot_events(command_id TEXT,event_id TEXT,digest TEXT,body TEXT,PRIMARY KEY(command_id,event_id));
            ''')

    def clean(self,db):
        now=NOW().isoformat()
        db.execute("UPDATE iot_commands SET status='EXPIRED' WHERE status='QUEUED' AND expires_at<?",(now,))
        db.execute("UPDATE iot_commands SET status='FAILED_TIMEOUT' WHERE status IN ('ACCEPTED','RUNNING','CANCEL_REQUESTED') AND deadline<?",(now,))

    def output(self,row,token=False):
        if not row:return None
        result=json.loads(row['body'])
        result.update(command_id=row['command_id'],status=row['status'],created_at=row['created_at'],expires_at=row['expires_at'],
            stop_requested=row['status']=='CANCEL_REQUESTED',terminal=row['status'] in TERMINAL)
        if token:result['lease_token']=row['lease_token']
        return result

    def connect(self):
        import sqlite3
        db=self.store.connect();db.row_factory=sqlite3.Row
        return db

    def hello(self,device,body):
        if body.get('device_id')!=device:raise PermissionError('Identitas perangkat tidak cocok.')
        boot=body.get('boot_id')
        if not isinstance(boot,str) or not 1<=len(boot)<=80:raise ValueError('boot_id wajib diisi.')
        if body.get('firmware_version')!='balance-iot/2.0':raise ValueError('Versi firmware harus balance-iot/2.0.')
        if not isinstance(body.get('armed'),bool):raise ValueError('armed harus boolean.')
        number(body.get('pulses_per_liter'),'pulses_per_liter',200000)
        if not isinstance(body.get('calibration_ref',''),str) or len(body.get('calibration_ref',''))>160:raise ValueError('Referensi kalibrasi tidak valid.')
        for k in ['source_distance_cm','released_since_boot_ml']:
            if body.get(k) is not None:number(body[k],k)
        record={k:body.get(k) for k in ['device_id','boot_id','firmware_version','armed','pulses_per_liter','calibration_ref','source_distance_cm','released_since_boot_ml','clock_synced']}
        record['device_kind']=body.get('device_kind','ESP32')
        if record['device_kind'] not in ('ESP32','SIMULATOR'):raise ValueError('device_kind tidak valid.')
        record.update(context='SIMULASI' if record['device_kind']=='SIMULATOR' else 'MINIATUR',received_at=NOW().isoformat(),measurement_location='SOURCE',quality='UNCALIBRATED',used_for_training=False,used_for_reoptimization=False)
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.clean(db)
            db.execute("UPDATE iot_commands SET status='FAILED_REBOOT' WHERE device_id=? AND boot_id IS NOT NULL AND boot_id<>? AND status IN ('ACCEPTED','RUNNING','CANCEL_REQUESTED')",(device,boot))
            db.execute('INSERT INTO iot_devices VALUES(?,?,?,?) ON CONFLICT(device_id) DO UPDATE SET boot_id=excluded.boot_id,body=excluded.body,last_seen=excluded.last_seen',
                (device,boot,dumps(record),record['received_at']))
        return record

    def devices(self):
        with self.connect() as db:rows=db.execute('SELECT * FROM iot_devices').fetchall()
        result=[]
        for r in rows:
            body=json.loads(r['body']);body['age_s']=max(0,(NOW()-datetime.fromisoformat(r['last_seen'])).total_seconds());body['online']=body['age_s']<=15
            result.append(body)
        return result

    def queue(self,body):
        if set(body)!={'device_id','plan_id','mode'} or body['mode'] not in ('DRY_RUN','MINIATUR'):raise ValueError('device_id, plan_id, mode DRY_RUN/MINIATUR wajib diisi.')
        device,plan_id,mode=body['device_id'],body['plan_id'],body['mode']
        with self.store.connect() as db:r=db.execute("SELECT body FROM records WHERE kind='plan' AND json_extract(body,'$.plan_id')=?",(plan_id,)).fetchone()
        if not r:raise ValueError('Rencana tidak ditemukan; hitung rencana dahulu.')
        plan=json.loads(r[0])
        if (NOW()-datetime.fromisoformat(plan['created_at'])).total_seconds()>1200:raise ValueError('Rencana lebih dari 20 menit. Hitung ulang skenario.')
        if plan['allocation']['status'] in ('NO_SUPPLY','DEGRADED_TAIL'):raise ValueError('Rencana tanpa pasokan/pengaman hilir gagal tidak dapat dikirim.')
        slots=[{'zone_id':s['zone_id'],'target_release_ml':s['release_ml'],'travel_delay_s':s['travel_delay_assumed_s']} for s in plan['schedule']['slots'] if s['release_ml']>0]
        if not slots or len(slots)>3:raise ValueError('Rencana tidak memiliki slot yang valid.')
        if sum(s['target_release_ml'] for s in slots)>1000.00001 or any(s['target_release_ml']>750 for s in slots):raise ValueError('Batas miniatur: total 1000 mL dan per zona 750 mL. Kurangi skenario.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.clean(db)
            old=db.execute('SELECT * FROM iot_commands WHERE device_id=? AND plan_id=?',(device,plan_id)).fetchone()
            if old:
                if json.loads(old['body'])['mode']!=mode:raise Conflict('Rencana sudah dipakai pada mode lain. Buat rencana baru.')
                return self.output(old)
            d=db.execute('SELECT * FROM iot_devices WHERE device_id=?',(device,)).fetchone()
            if not d or (NOW()-datetime.fromisoformat(d['last_seen'])).total_seconds()>15:raise ValueError('Perangkat belum online. Jalankan firmware terlebih dahulu.')
            hello=json.loads(d['body'])
            if mode=='MINIATUR' and (hello['device_kind']!='ESP32' or not self.enabled or not hello['armed'] or not hello.get('calibration_ref') or hello['pulses_per_liter']<=0):
                raise ValueError('MINIATUR memerlukan ENABLE_ACTUATION=true, ARM lokal, dan referensi kalibrasi sensor.')
            if db.execute("SELECT 1 FROM iot_commands WHERE device_id=? AND status IN ('QUEUED','ACCEPTED','RUNNING','CANCEL_REQUESTED')",(device,)).fetchone():raise Conflict('Perangkat masih memiliki perintah aktif.')
            now=NOW();key='cmd_'+uuid.uuid4().hex
            command={**body,'schema_version':'balance-device-command/2','unit':'mL','planning_context':'SIMULASI',
                'execution_context':'SIMULASI' if mode=='DRY_RUN' else 'MINIATUR','physical_execution':mode=='MINIATUR',
                'slots':slots,'source_budget_ml':sum(s['target_release_ml'] for s in slots),'max_cycle_ms':600000,
                'measurement_location':'SOURCE','expected_receipts_are_predictions':True,'calibration_snapshot':{'pulses_per_liter':hello['pulses_per_liter'],'calibration_ref':hello.get('calibration_ref','')}}
            db.execute('INSERT INTO iot_commands(command_id,device_id,plan_id,status,body,created_at,expires_at) VALUES(?,?,?,?,?,?,?)',
                (key,device,plan_id,'QUEUED',dumps(command),now.isoformat(),(now+timedelta(seconds=180)).isoformat()))
            return self.output(db.execute('SELECT * FROM iot_commands WHERE command_id=?',(key,)).fetchone())

    def next(self,device,boot):
        with self.connect() as db:
            self.clean(db)
            d=db.execute('SELECT * FROM iot_devices WHERE device_id=?',(device,)).fetchone()
            if not d or d['boot_id']!=boot or (NOW()-datetime.fromisoformat(d['last_seen'])).total_seconds()>15:raise ValueError('Kirim heartbeat dengan boot_id yang sama terlebih dahulu.')
            row=db.execute("SELECT * FROM iot_commands WHERE device_id=? AND status='QUEUED' ORDER BY created_at LIMIT 1",(device,)).fetchone()
            return self.output(row)

    def claim(self,device,key,body):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.clean(db)
            r=db.execute('SELECT * FROM iot_commands WHERE command_id=? AND device_id=?',(key,device)).fetchone()
            if not r:raise ValueError('Perintah tidak ditemukan.')
            d=db.execute('SELECT * FROM iot_devices WHERE device_id=?',(device,)).fetchone()
            if not d or d['boot_id']!=body.get('boot_id') or (NOW()-datetime.fromisoformat(d['last_seen'])).total_seconds()>15:raise ValueError('Heartbeat/boot tidak valid.')
            c=json.loads(r['body']);hello=json.loads(d['body'])
            if c['mode']=='MINIATUR' and (hello['device_kind']!='ESP32' or not self.enabled or not hello['armed'] or not hello.get('calibration_ref') or c['calibration_snapshot']!={'pulses_per_liter':hello['pulses_per_liter'],'calibration_ref':hello.get('calibration_ref','')}):raise ValueError('Perangkat belum di-arm atau kalibrasi belum ditandai.')
            if r['status']=='ACCEPTED' and r['boot_id']==body.get('boot_id'):return self.output(r,True)
            if r['status']!='QUEUED':raise Conflict('Perintah telah diklaim/selesai; tidak dijalankan ulang.')
            token=secrets.token_urlsafe(24)
            db.execute("UPDATE iot_commands SET status='ACCEPTED',boot_id=?,lease_token=?,deadline=? WHERE command_id=?",(body['boot_id'],token,(NOW()+timedelta(seconds=630)).isoformat(),key))
            return self.output(db.execute('SELECT * FROM iot_commands WHERE command_id=?',(key,)).fetchone(),True)

    def event(self,device,key,body):
        allowed={'event_id','boot_id','lease_token','type','released_ml','message'}
        if set(body)-allowed or not {'event_id','boot_id','lease_token','type','released_ml'}.issubset(body):raise ValueError('Field event tidak sesuai.')
        if not isinstance(body['event_id'],str) or not 1<=len(body['event_id'])<=100:raise ValueError('event_id tidak valid.')
        if body['type'] not in ('STARTED','PROGRESS','COMPLETED','FAILED','CANCELLED'):raise ValueError('Tipe event tidak valid.')
        values=body['released_ml']
        if not isinstance(values,dict) or set(values)!={'A','B','C'}:raise ValueError('released_ml wajib A/B/C dalam mL.')
        for k,v in values.items():number(v,'released_ml.'+k,1000000)
        if len(str(body.get('message','')))>300:raise ValueError('Pesan terlalu panjang.')
        digest=hashlib.sha256(dumps(body).encode()).hexdigest()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.clean(db)
            row=db.execute('SELECT * FROM iot_commands WHERE command_id=? AND device_id=?',(key,device)).fetchone()
            if not row:raise ValueError('Perintah tidak ditemukan.')
            if row['boot_id']!=body['boot_id'] or not isinstance(body['lease_token'],str) or len(body['lease_token'])<20 or not secrets.compare_digest(row['lease_token'] or '',body['lease_token']):raise PermissionError('Lease tidak cocok.')
            old=db.execute('SELECT digest FROM iot_events WHERE command_id=? AND event_id=?',(key,body['event_id'])).fetchone()
            if old:
                if old['digest']!=digest:raise Conflict('event_id telah dipakai dengan isi berbeda.')
                return self.output(row)
            if row['status'] in TERMINAL:raise Conflict('Perintah terminal tidak menerima event baru.')
            p=json.loads(row['body']);previous=p.get('reported_release_ml',dict(A=0,B=0,C=0))
            targets={s['zone_id']:s['target_release_ml'] for s in p['slots']}
            if any(values[z]+1e-6<previous[z] or (body['type'] not in ('FAILED','CANCELLED') and values[z]>targets.get(z,0)+5) for z in 'ABC'):raise ValueError('Volume kumulatif mundur atau melampaui batas perintah.')
            typ=body['type']
            if typ=='STARTED' and (row['status']!='ACCEPTED' or any(values[z]!=0 for z in 'ABC')):raise Conflict('STARTED hanya setelah claim.')
            if typ=='PROGRESS' and row['status'] not in ('RUNNING','CANCEL_REQUESTED'):raise Conflict('PROGRESS hanya saat berjalan.')
            if typ=='COMPLETED' and (row['status'] not in ('RUNNING','CANCEL_REQUESTED') or any(abs(values[z]-targets.get(z,0))>5 for z in 'ABC')):raise ValueError('COMPLETED memerlukan seluruh kuota selesai dalam toleransi 5 mL.')
            status={'STARTED':'RUNNING','PROGRESS':row['status'],'COMPLETED':'COMPLETED','FAILED':'FAILED','CANCELLED':'CANCELLED'}[typ]
            p.update(reported_release_ml=values,last_event=typ,last_event_received_at=NOW().isoformat(),last_message=body.get('message',''),
                reported_volume_kind='SIMULATED' if p['mode']=='DRY_RUN' else 'MEASUREMENT_UNCALIBRATED_BY_SERVER')
            db.execute('INSERT INTO iot_events VALUES(?,?,?,?)',(key,body['event_id'],digest,dumps({k:v for k,v in body.items() if k!='lease_token'})))
            db.execute('UPDATE iot_commands SET status=?,body=? WHERE command_id=?',(status,dumps(p),key))
            return self.output(db.execute('SELECT * FROM iot_commands WHERE command_id=?',(key,)).fetchone())

    def cancel(self,key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE');self.clean(db)
            r=db.execute('SELECT * FROM iot_commands WHERE command_id=?',(key,)).fetchone()
            if not r:raise ValueError('Perintah tidak ditemukan.')
            state='CANCELLED' if r['status']=='QUEUED' else ('CANCEL_REQUESTED' if r['status'] not in TERMINAL else r['status'])
            db.execute('UPDATE iot_commands SET status=? WHERE command_id=?',(state,key))
            return self.output(db.execute('SELECT * FROM iot_commands WHERE command_id=?',(key,)).fetchone())

    def list(self,device=None,key=None):
        with self.connect() as db:
            self.clean(db)
            where,args=(' WHERE device_id=?',[device]) if device else ('',[])
            if key:where+=(' AND ' if where else ' WHERE ')+'command_id=?';args.append(key)
            return [self.output(r) for r in db.execute('SELECT * FROM iot_commands'+where+' ORDER BY created_at DESC LIMIT 30',args)]


def routes(app,service,config,read_json,role):
    broker=Broker(service,config.get('enable_actuation',False));app.extensions['broker']=broker
    bp=Blueprint('connected_v2',__name__,url_prefix='/api/iot/v2')
    def device():
        token=request.headers.get('X-Balance-Key','')
        return next((d for d,k in config['device_keys'].items() if secrets.compare_digest(token.encode(),k.encode())),None)
    @bp.before_request
    def auth():
        if '/device/' in request.path:
            if not device():return jsonify(error='UNAUTHORIZED_DEVICE'),401
        elif role()!='operator':return jsonify(error='OPERATOR_REQUIRED'),403
    @bp.errorhandler(PermissionError)
    def denied(exc):return jsonify(error='DEVICE_LEASE_MISMATCH',message=str(exc)),403
    @bp.get('/devices')
    def devices():return jsonify(devices=broker.devices(),miniature_enabled=broker.enabled,source_sensor_count=1)
    @bp.post('/commands')
    def queue():
        b=read_json()
        if b.get('device_id') not in config['device_keys']:raise ValueError('Perangkat tidak terdaftar.')
        return jsonify(broker.queue(b))
    @bp.get('/commands')
    def commands():return jsonify(commands=broker.list())
    @bp.post('/commands/<key>/cancel')
    def cancel(key):return jsonify(broker.cancel(key))
    @bp.post('/device/hello')
    def hello():return jsonify(broker.hello(device(),read_json()))
    @bp.get('/device/next-command')
    def next_command():return jsonify(command=broker.next(device(),request.args.get('boot_id')))
    @bp.post('/device/commands/<key>/claim')
    def claim(key):return jsonify(broker.claim(device(),key,read_json()))
    @bp.get('/device/commands/<key>/control')
    def control(key):
        results=broker.list(device(),key)
        if not results:return jsonify(error='NOT_FOUND'),404
        return jsonify(command=results[0])
    @bp.post('/device/commands/<key>/events')
    def event(key):return jsonify(broker.event(device(),key,read_json()))
    app.register_blueprint(bp)
    return broker
