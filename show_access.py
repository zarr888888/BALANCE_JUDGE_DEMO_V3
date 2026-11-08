"""Print one local key only when the owner explicitly runs this command."""
import argparse
import json
from pathlib import Path

if __name__ == '__main__':
    p=argparse.ArgumentParser()
    p.add_argument('role',choices=['dashboard','iot','judge'])
    a=p.parse_args()
    c=json.loads((Path(__file__).resolve().parent/'backend'/'config.local.json').read_text())
    if a.role=='judge':print('Username:',c.get('judge_username','juri'),'\nPassword:',c.get('judge_password','Diatur melalui environment hosting.'))
    else:print(c['dashboard_key'] if a.role=='dashboard' else c['device_keys']['esp32-demo'])
