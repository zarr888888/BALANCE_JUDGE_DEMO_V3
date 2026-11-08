from balance_handoff.prepare import prepare
from pathlib import Path
if __name__=='__main__':prepare(Path(__file__).resolve().parent/'.state')
