from datetime import datetime,timezone
import hashlib,json,platform
from pathlib import Path
import joblib,numpy as np,scipy,sklearn
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from . import __version__
from .data import FEATURES,ZONES,inspect_pair,matrix,split_audit


def targets(rows):
    return np.asarray([inspect_pair(r)['eta_label'] for r in rows])


def score(rows,raw):
    raw=np.asarray(raw,dtype=float)
    if not np.isfinite(raw).all() or raw.shape!=(len(rows),):
        raise ValueError('Invalid model output')
    actual=targets(rows)
    effective=np.clip(raw,0,1)
    releases=np.asarray([r['release_volume_ml'] for r in rows])
    delta=(effective-actual)*releases
    raw_delta=(raw-actual)*releases
    return {'n':len(rows),'eta_mae_ratio':float(np.mean(abs(effective-actual))),
            'eta_rmse_ratio':float(np.sqrt(np.mean((effective-actual)**2))),
            'received_mae_ml':float(np.mean(abs(delta))),
            'received_rmse_ml':float(np.sqrt(np.mean(delta**2))),
            'received_bias_ml':float(np.mean(delta)),
            'raw_received_mae_ml':float(np.mean(abs(raw_delta))),
            'clipped_prediction_n':int(np.sum(raw!=effective))}


def median_by_zone(rows):
    return {z:float(np.median([inspect_pair(r)['eta_label'] for r in rows if r['zone_id']==z])) for z in ZONES}


def train(rows,output,seed=20261101):
    parts,_=split_audit(rows)
    out=Path(output);out.mkdir(parents=True,exist_ok=False)
    content=''.join(json.dumps(r,sort_keys=True,allow_nan=False)+'\n' for r in rows).encode()
    (out/'pairs_SIMULASI.jsonl').write_bytes(content)
    medians=median_by_zone(parts['train'])
    fixed={'A':1.0,'B':1.0,'C':.8}  # old demo assumptions, not learned parameters
    models={'ridge':make_pipeline(StandardScaler(),Ridge(alpha=1.0)),
            'gradient_boosting':GradientBoostingRegressor(n_estimators=120,max_depth=2,
                learning_rate=.05,min_samples_leaf=4,random_state=seed)}
    xtrain=matrix(parts['train']);ytrain=targets(parts['train'])
    validation={}
    for name,model in models.items():
        model.fit(xtrain,ytrain)
        validation[name]=score(parts['validation'],model.predict(matrix(parts['validation'])))
    reference={'train_branch_median':medians,'fixed_demo_assumption':fixed}
    for name,values in reference.items():
        validation[name]=score(parts['validation'],[values[r['zone_id']] for r in parts['validation']])
    selected=min(models,key=lambda n:(validation[n]['received_mae_ml'],n))
    preferred=min(validation,key=lambda n:(validation[n]['received_mae_ml'],n))
    # Choice is frozen here before the test feature matrix is constructed.
    test={};per_zone={};records=[]
    xtest=matrix(parts['test'])
    for name in list(models)+list(reference):
        raw=models[name].predict(xtest) if name in models else np.asarray([reference[name][r['zone_id']] for r in parts['test']])
        test[name]=score(parts['test'],raw)
        per_zone[name]={}
        for z in ZONES:
            selected_rows=[r for r in parts['test'] if r['zone_id']==z]
            selected_raw=[float(v) for r,v in zip(parts['test'],raw) if r['zone_id']==z]
            per_zone[name][z]=score(selected_rows,selected_raw)
        for r,value in zip(parts['test'],raw):
            records.append({'record_id':r['record_id'],'context':'SIMULASI','method':name,
                'eta_label_CALCULATED':inspect_pair(r)['eta_label'],'eta_raw_prediction':float(value),
                'eta_prediction':float(np.clip(value,0,1)),
                'simulated_release_ml':r['release_volume_ml'],'simulated_received_ml':r['received_volume_ml'],
                'actual_measured_ml':None})
    environment={'python':platform.python_version(),'numpy':np.__version__,'scipy':scipy.__version__,
                 'scikit_learn':sklearn.__version__,'joblib':joblib.__version__}
    report={'schema_version':'balance-loss-report/0.1','model_version':__version__,
        'created_at':datetime.now(timezone.utc).isoformat(),'context':'SIMULASI',
        'ml_status':'TRAINED_SYNTHETIC_ONLY','real_measurement_rows':0,'field_validated':False,
        'proposal_targets_proven':False,'execution_allowed':False,'flask_integration':'NOT_CONNECTED_YET',
        'dataset_sha256':hashlib.sha256(content).hexdigest(),'environment':environment,
        'seed':seed,'features':list(FEATURES),'selected_ml':selected,
        'preferred_including_baselines':preferred,
        'selection_rule':'Lowest validation received MAE in mL; no selection/refit using test',
        'split':{s:{'rows':len(p),'sessions':len({r['session_id'] for r in p}),
                    'experiments':len({r['experiment_id'] for r in p}),
                    'first_as_of':min(r['as_of'] for r in p),'last_recorded_at':max(r['recorded_at'] for r in p)} for s,p in parts.items()},
        'train_median_eta':medians,'fixed_assumption_eta':fixed,'validation':validation,'test':test,'test_per_zone':per_zone,
        'hyperparameters':{'ridge':{'alpha':1.0,'preprocessing':'StandardScaler fit on train only'},
                          'gradient_boosting':models['gradient_boosting'].get_params()},
        'source_sha256':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).resolve().parent.glob('*.py')},
        'parameter_registry_sha256':hashlib.sha256((Path(__file__).resolve().parent/'parameter_registry.json').read_bytes()).hexdigest(),
        'limitations':['Synthetic generator only; model scores do not prove real loss correction.',
          'Travel delay is an alignment assumption, not a trained delay model.',
          'Future release/received volumes are labels/evaluation inputs, never ML features.',
          'No sensor calibration, hydraulic similarity, uncertainty interval or device operation.',
          'The existing browser demo still uses its original fixed eta assumptions.']}
    joblib.dump({'model':models[selected],'feature_names':FEATURES,'training_min':xtrain.min(axis=0),
        'training_max':xtrain.max(axis=0),'model_version':__version__,'context':'SIMULASI',
        'ml_status':'TRAINED_SYNTHETIC_ONLY','environment':environment,'dataset_sha256':report['dataset_sha256']},out/'loss_eta_synthetic_model.joblib')
    (out/'training_report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n',encoding='utf-8')
    (out/'predictions_test_SIMULASI.jsonl').write_text(''.join(json.dumps(r,allow_nan=False)+'\n' for r in records),encoding='utf-8')
    # Demonstration features are declared in advance, unrelated to a test label.
    sample=[{'zone_id':z,'features':{'nominal_flow_ml_s':6.0,'upstream_head_cm':20.0},
             'features_available_at':'2026-11-01T00:00:00+07:00','as_of':'2026-11-01T00:00:00+07:00'} for z in ZONES]
    sample_matrix=matrix(sample);raw=models[selected].predict(sample_matrix)
    profile=[]
    for row,values,value in zip(sample,sample_matrix,raw):
        effective=float(np.clip(value,0,1))
        outside=[key for key,x,low,high in zip(FEATURES,values,xtrain.min(axis=0),xtrain.max(axis=0)) if x<low or x>high]
        profile.append({'zone_id':row['zone_id'],'feature_snapshot':row,'eta_raw_prediction':float(value),
            'delivery_efficiency':effective if effective>0 else None,'clipping_applied':bool(value!=effective),
            'outside_training_range':outside,'actual_measured_eta':None,
            'provenance':{'context':'SIMULASI','kind':'PREDICTION','quality':'UNVERIFIED','source_ref':__version__}})
    (out/'eta_profile_SIMULASI.json').write_text(json.dumps({'schema_version':'balance-loss-profile/0.1',
        'context':'SIMULASI','ml_status':'TRAINED_SYNTHETIC_ONLY','model_method':selected,
        'integration_status':'NOT_CONNECTED_TO_FLASK_YET','execution_allowed':False,
        'profiles':profile,'prediction_interval':None},indent=2,allow_nan=False)+'\n',encoding='utf-8')
    lines=['BALANCE LOSS LAB - SIMULASI','ML_STATUS=TRAINED_SYNTHETIC_ONLY','REAL_MEASUREMENT_ROWS=0',
           f'SELECTED_ML={selected}',f'PREFERRED_INCLUDING_BASELINES={preferred}','']
    for name,m in test.items():
        lines.append(f"{name}: test received MAE={m['received_mae_ml']:.3f} mL; RMSE={m['received_rmse_ml']:.3f} mL; eta MAE={m['eta_mae_ratio']:.5f}")
    lines+=['','Prototype eta at assumed flow=6 mL/s, head=20 cm:']
    lines += [f"  {p['zone_id']}: eta={p['delivery_efficiency']:.5f}" if p['delivery_efficiency'] is not None else f"  {p['zone_id']}: invalid for LP" for p in profile]
    lines+=['','FLASK_INTEGRATION=NOT_CONNECTED_YET','FIELD_VALIDATED=false','LOSS_TRAINING_COMPLETE','']
    (out/'summary.txt').write_text('\n'.join(lines),encoding='utf-8')
    return report
