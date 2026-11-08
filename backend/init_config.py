import json,secrets
from pathlib import Path
if __name__=='__main__':
    path=Path(__file__).resolve().parent/'config.local.json'
    if path.exists():print('config.local.json already exists; keys unchanged.')
    else:
        path.write_text(json.dumps({'dashboard_key':secrets.token_urlsafe(32),'device_keys':{'esp32-demo':secrets.token_urlsafe(32)}},indent=2)+'\n')
        print('Created config.local.json. Dashboard key for backend; esp32-demo key for the IoT owner. Do not publish this file.')
