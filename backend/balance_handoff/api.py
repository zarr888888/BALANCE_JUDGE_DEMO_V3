"""Flask Blueprint, same-app or standalone integration, with distinct server/device keys."""
import json, logging, secrets
from flask import Blueprint, jsonify, request
from .service import Conflict, ReviewRequired

MAX_BYTES=65536
def create_blueprint(service,*,dashboard_key,device_keys,url_prefix):
    keys=[dashboard_key,*device_keys.values()]
    if any(not isinstance(k,str) or len(k)<20 for k in keys) or len(set(keys))!=len(keys):
        raise ValueError('Use distinct dashboard/device keys of at least 20 characters')
    bp=Blueprint('balance_handoff_v1',__name__,url_prefix=url_prefix)
    def identity():
        token=request.headers.get('X-Balance-Key','')
        if secrets.compare_digest(token.encode(),dashboard_key.encode()):return 'dashboard',None
        for device,key in device_keys.items():
            if secrets.compare_digest(token.encode(),key.encode()):return 'device',device
        return None,None
    def error(name,status):return jsonify(error=name,execution_allowed=False),status
    def payload():
        if not request.is_json:raise TypeError('JSON_REQUIRED')
        raw=request.stream.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise OverflowError('PAYLOAD_TOO_LARGE')
        def constant(_):raise ValueError('NONFINITE_JSON')
        def unique(pairs):
            d={}
            for k,v in pairs:
                if k in d:raise ValueError('DUPLICATE_JSON_KEY')
                d[k]=v
            return d
        return json.loads(raw,parse_constant=constant,object_pairs_hook=unique)
    def run(action,role='dashboard'):
        who,device=identity()
        if who is None:return error('UNAUTHORIZED',401)
        if who!=role:return error('FORBIDDEN_ROLE',403)
        try:
            try:body=payload()
            except TypeError:return error('JSON_REQUIRED',415)
            except OverflowError:return error('PAYLOAD_TOO_LARGE',413)
            except (ValueError,UnicodeDecodeError):return error('INVALID_JSON',400)
            return action(body,device)
        except PermissionError:return error('DEVICE_ID_MISMATCH',403)
        except Conflict:return error('ID_CONFLICT',409)
        except ValueError as exc:return jsonify(error='INVALID_INPUT',message=str(exc)[:300],execution_allowed=False),422
        except (ReviewRequired,RuntimeError,KeyError,TypeError,ArithmeticError):
            logging.getLogger(__name__).exception('BALANCE calculation requires review')
            return error('REVIEW_REQUIRED_NO_NEW_PLAN',503)
    @bp.get('/health')
    def health():return jsonify(service.health())
    @bp.post('/plan')
    def plan():return run(lambda body,_:jsonify(service.plan(body)))
    @bp.get('/plans/latest')
    def latest_plan():
        if identity()[0]!='dashboard':return error('UNAUTHORIZED',401)
        plan=service.store.latest('plan')
        return jsonify(plan) if plan else error('NO_PLAN_YET',404)
    @bp.post('/scheduler/simulate')
    def scheduler():return run(lambda body,_:jsonify(service.scheduler(body)))
    @bp.post('/history/simulate')
    def history():return run(lambda body,_:jsonify(service.history(body)))
    @bp.post('/telemetry')
    def telemetry():
        def ingest(body,device):
            record,created=service.record_telemetry(body,device)
            return jsonify(status='STORED' if created else 'ALREADY_STORED',record=record),201 if created else 200
        return run(ingest,role='device')
    @bp.get('/telemetry/latest')
    def latest_telemetry():
        role,device=identity()
        if role is None:return error('UNAUTHORIZED',401)
        target=request.args.get('device_id',device)
        if not target:return error('DEVICE_ID_REQUIRED',422)
        if role=='device' and target!=device:return error('FORBIDDEN_DEVICE',403)
        result=service.latest_telemetry(target)
        return jsonify(result) if result else error('NO_TELEMETRY_YET',404)
    @bp.get('/device/next-command')
    def next_command():
        role,device=identity()
        if role!='device':return error('UNAUTHORIZED_DEVICE',401)
        return jsonify(device_id=device,command=None,execution_allowed=False,status='NO_ACTUATION_IN_THIS_RELEASE')
    @bp.after_request
    def headers(response):
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        return response
    return bp
