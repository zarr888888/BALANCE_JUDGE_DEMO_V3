import os
from pathlib import Path
import shutil
import sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'backend'))
from runtime_config import state_path

def initialize():
    target=state_path();target.mkdir(parents=True,exist_ok=True)
    template=Path(os.getenv('BALANCE_MODEL_TEMPLATE','/opt/balance-model-template'))
    if not (target/'active.json').exists():
        if template.exists():shutil.copytree(template,target,dirs_exist_ok=True)
        else:
            from balance_handoff.prepare import prepare
            prepare(target)
    from app import create_app
    return create_app(target)

if __name__=='__main__':
    from waitress import serve
    app=initialize()
    port=int(os.getenv('PORT','10000'))
    print(f'BALANCE judge demo V3 ready on port {port}; persistent state initialized.',flush=True)
    serve(app,host='0.0.0.0',port=port,threads=4)
