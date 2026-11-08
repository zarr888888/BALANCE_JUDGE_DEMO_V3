"""Load trusted local loss artifacts; predict one frozen eta snapshot per solve."""
import hashlib,json,platform
from pathlib import Path
import joblib,numpy as np,scipy,sklearn
from balance_handoff.vendor.balance_loss.data import FEATURES,ZONES,matrix,split_audit


class LossRuntime:
    def __init__(self,run_dir):
        self.run_dir=Path(run_dir)
        self.report=json.loads((self.run_dir/'training_report.json').read_text(encoding='utf-8'))
        r=self.report
        if r['schema_version']!='balance-loss-report/0.1' or r['context']!='SIMULASI' or r['ml_status']!='TRAINED_SYNTHETIC_ONLY':
            raise ValueError('Loss runtime accepts only completed synthetic lab artifacts')
        if r['real_measurement_rows']!=0 or r['field_validated'] is not False or tuple(r['features'])!=FEATURES:
            raise ValueError('Loss report provenance/feature mismatch')
        current={'numpy':np.__version__,'scipy':scipy.__version__,'scikit_learn':sklearn.__version__,'joblib':joblib.__version__}
        for name,version in current.items():
            if r['environment'][name]!=version:
                raise ValueError(f'Versi loss {name} berbeda dari training; gunakan environment training yang sama')
        if '.'.join(r['environment']['python'].split('.')[:2])!='.'.join(platform.python_version().split('.')[:2]):
            raise ValueError('Versi Python loss berbeda dari training')
        raw=(self.run_dir/'pairs_SIMULASI.jsonl').read_bytes()
        if hashlib.sha256(raw).hexdigest()!=r['dataset_sha256']:
            raise ValueError('Loss dataset hash mismatch')
        rows=[json.loads(s) for s in raw.decode().splitlines()]
        split_audit(rows)
        path=self.run_dir/'loss_eta_synthetic_model.joblib'
        # Startup CLI path only; there is no HTTP upload/download/model-path input.
        bundle=joblib.load(path)
        if bundle['context']!='SIMULASI' or bundle['ml_status']!='TRAINED_SYNTHETIC_ONLY' or bundle['model_version']!=r['model_version']:
            raise ValueError('Loss model provenance mismatch')
        if tuple(bundle['feature_names'])!=FEATURES or bundle['dataset_sha256']!=r['dataset_sha256'] or bundle['environment']!=r['environment']:
            raise ValueError('Loss model/report mismatch')
        self.low=np.asarray(bundle['training_min'],dtype=float)
        self.high=np.asarray(bundle['training_max'],dtype=float)
        if self.low.shape!=(len(FEATURES),) or self.high.shape!=self.low.shape or not np.isfinite(self.low).all() or not np.isfinite(self.high).all() or (self.high<self.low).any():
            raise ValueError('Invalid loss training bounds')
        self.model=bundle['model']
        self.model_hash=hashlib.sha256(path.read_bytes()).hexdigest()

    def info(self):
        return {'model_run':self.run_dir.name,'model_version':self.report['model_version'],
                'selected_ml':self.report['selected_ml'],'model_sha256':self.model_hash,
                'dataset_sha256':self.report['dataset_sha256'],'training_context':'SIMULASI',
                'field_validated':False,'real_measurement_rows':0}

    def predict_snapshot(self,as_of,flow,head):
        rows=[{'zone_id':z,'features':{'nominal_flow_ml_s':flow,'upstream_head_cm':head},
               'features_available_at':as_of,'as_of':as_of} for z in ZONES]
        x=matrix(rows)
        try:
            raw=np.asarray(self.model.predict(x),dtype=float)
        except (ValueError,TypeError,IndexError) as error:
            raise RuntimeError('Loss inference failed; no fallback eta applied') from error
        if raw.shape!=(3,) or not np.isfinite(raw).all():
            raise RuntimeError('Invalid loss inference output')
        effective=np.clip(raw,0,1)
        if (effective<=0).any():
            raise RuntimeError('Nonpositive loss eta incompatible with LP; no fallback eta applied')
        output=[]
        for row,values,value,eta in zip(rows,x,raw,effective):
            outside=[key for key,v,low,high in zip(FEATURES,values,self.low,self.high) if v<low or v>high]
            output.append({'zone_id':row['zone_id'],'delivery_efficiency':float(eta),
                'eta_raw_prediction':float(value),'clipping_applied':bool(value!=eta),
                'outside_training_range':outside,'actual_measured_eta':None,
                'feature_snapshot':row,'provenance':{'context':'SIMULASI','kind':'PREDICTION',
                    'source_ref':f"{self.run_dir.name}/{self.report['model_version']}",
                    'as_of':as_of,'quality':'UNVERIFIED'}})
        return output
