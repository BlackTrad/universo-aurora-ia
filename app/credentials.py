"""Write-only credential changes; atomically preserve unrelated .env entries."""
import os
import tempfile
from pathlib import Path
from dataclasses import replace
from dotenv import set_key

KEYS={'telegram_token':'TELEGRAM_BOT_TOKEN','telegram_chat':'TELEGRAM_CHAT_ID'}

def update(path, config, changes):
    if not isinstance(changes,dict) or not changes or set(changes)-KEYS.keys():
        raise ValueError('Informe somente os campos de conexão permitidos.')
    if any(not isinstance(v,str) or len(v)>256 or '\n' in v or '\r' in v for v in changes.values()):
        raise ValueError('Campos de conexão inválidos.')
    values={k:v.strip() for k,v in changes.items()}
    candidate=replace(config,**values)
    candidate.validate()
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.aurora-config-',dir=path.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as output:
            if path.exists(): output.write(path.read_text(encoding='utf-8'))
        for field,value in values.items():set_key(temp,KEYS[field],value,quote_mode='always')
        os.chmod(temp,0o600)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
    for field,value in values.items():
        setattr(config,field,value)
        os.environ[KEYS[field]]=value

def status(config):
    return {'telegram_configured':bool(config.telegram_token and config.telegram_chat),'explanation_source':'motor'}
