"""Prepare the separate judge demo, optionally reusing trusted V2 model artifacts."""
import argparse
from pathlib import Path
import runpy
import shutil

ROOT = Path(__file__).resolve().parent

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--reuse-from', type=Path, help='Existing BALANCE_CONNECTED_V2 project folder')
    args = parser.parse_args()
    target = ROOT / 'backend' / '.state'
    if args.reuse_from and not (target / 'active.json').exists():
        source = args.reuse_from.resolve() / 'backend' / '.state'
        if not (source / 'active.json').is_file():
            parser.error('Sumber tidak memiliki backend/.state/active.json. Periksa folder V2 yang sudah berjalan.')
        if target.exists():
            parser.error('Folder state tujuan sudah ada tetapi belum lengkap; tidak ditimpa secara otomatis.')
        shutil.copytree(source, target, ignore=shutil.ignore_patterns('*.sqlite3*', '*.db*'))
        print('Artefak model V2 disalin ke demo terpisah; database dan konfigurasi akses lama tidak disalin.')
    runpy.run_path(str(ROOT / 'setup_demo.py'), run_name='__main__')
