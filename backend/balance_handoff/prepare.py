"""Prepare trusted model artifacts in the receiving machine's own environment."""
import argparse, hashlib, json, os
from datetime import datetime, timezone
from pathlib import Path
from .vendor.balance_ml.synthetic import generate as ml_data
from .vendor.balance_ml.training import train as train_ml
from .vendor.balance_loss.synthetic import generate as loss_data
from .vendor.balance_loss.training import train as train_loss

def prepare(state_dir):
    state=Path(state_dir).resolve();state.mkdir(parents=True,exist_ok=True)
    stamp=datetime.now(timezone.utc).strftime('run_%Y%m%dT%H%M%S_%fZ')
    run=state/'models'/stamp
    print('Preparing reproducible SYNTHETIC demand/supply and loss models...',flush=True)
    ml=run/'ml';loss=run/'loss'
    train_ml(ml_data(seed=20261101),ml,seed=20261101)
    train_loss(loss_data(seed=20261101),loss,seed=20261101)
    manifest={'schema_version':'balance-local-artifacts/1','training_context':'SIMULASI',
              'run_dir':str(run.relative_to(state)),
              'sha256':{str(p.relative_to(run)):hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in run.rglob('*') if p.is_file()}}
    temp=state/'active.tmp.json';temp.write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    os.replace(temp,state/'active.json')
    print('MODELS_READY; TRAINED_SYNTHETIC_ONLY; FIELD_VALIDATED=false',flush=True)
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--state-dir',default='.state')
    prepare(parser.parse_args().state_dir)
