"""A toy transmission process; parameters are NOT calibrated to a real channel."""
from datetime import datetime,timedelta,timezone
import numpy as np


def generate(seed=20261101,sessions=25,runs_per_session=6):
    if any(isinstance(x,bool) or not isinstance(x,int) for x in (seed,sessions,runs_per_session)) or seed<0 or sessions<5 or runs_per_session<2:
        raise ValueError('Invalid generator settings')
    origin=datetime(2020,1,1,tzinfo=timezone(timedelta(hours=7)))
    rows=[]
    for session in range(sessions):
        rng=np.random.default_rng(np.random.SeedSequence([seed,session,831]))
        split='train' if session<int(.6*sessions) else 'validation' if session<int(.8*sessions) else 'test'
        family_bias=float(rng.uniform(-.004,.004))
        for run in range(runs_per_session):
            cutoff=origin+timedelta(days=session*2,hours=run)
            head=float(rng.uniform(10,30))
            for zone,base,delay in [('A',.98,2),('B',.90,4),('C',.80,6)]:
                flow=float(rng.uniform(2,12))
                actual_flow=flow+float(rng.uniform(-.2,.2))
                release=float(rng.uniform(50,500))
                eta=base-.0015*(actual_flow-6)**2+.0004*(head-20)+family_bias+float(rng.uniform(-.004,.004))
                assert 0<eta<1  # safe by the bounded toy parameter ranges, no label clipping
                start=cutoff+timedelta(seconds=60)
                end=start+timedelta(seconds=release/actual_flow)
                collection_end=end+timedelta(seconds=delay+2)
                identity=f'session_{session:03d}_run_{run:02d}'
                rows.append({
                    'schema_version':'balance-loss-pair/0.1','record_id':identity+'_'+zone,
                    'session_id':f'session_{session:03d}','experiment_id':identity,'split':split,
                    'context':'SIMULASI','volume_unit':'mL','zone_id':zone,
                    'release_value_kind':'SIMULATED','received_value_kind':'SIMULATED',
                    'release_volume_ml':release,'received_volume_ml':release*eta,
                    'as_of':cutoff.isoformat(),'features_available_at':cutoff.isoformat(),
                    'features':{'nominal_flow_ml_s':flow,'upstream_head_cm':head},
                    'release_start':start.isoformat(),'release_end':end.isoformat(),
                    'received_start':start.isoformat(),'received_end':collection_end.isoformat(),
                    'recorded_at':(collection_end+timedelta(seconds=1)).isoformat(),
                    'travel_delay_s':delay,'delay_ref':'GENERATOR_ASSUMPTION_NOT_LEARNED',
                    'quality':'VALID','collection_complete':True,'transit_reconciled':True,'isolated_pair':True,
                    'pairing_ref':identity,'release_calibration_ref':'SIMULATOR_NOT_REAL_CALIBRATION',
                    'received_calibration_ref':'SIMULATOR_NOT_REAL_CALIBRATION',
                    'source_ref':'balance-loss-generator/0.1','seed':seed})
    return rows
