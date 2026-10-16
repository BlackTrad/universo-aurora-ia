"""Create a shareable archive without the .env or database."""
import sys
from pathlib import Path
from datetime import datetime
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.config import Config,ROOT
from app.logging_setup import register_secrets
from app.diagnostics import build_archive

if __name__=='__main__':
    config=Config.load()
    register_secrets(config.telegram_token,config.telegram_chat,config.admin_token)
    output=ROOT/('AURORA-DIAGNOSTICO-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.zip')
    output.write_bytes(build_archive(ROOT/'logs'))
    print('Arquivo para compartilhar: '+str(output))
