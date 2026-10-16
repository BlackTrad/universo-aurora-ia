import os
from unittest.mock import patch
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from app.config import Config
from app.credentials import update
from app.main import create_app
from tests.test_system import Feed,TG

def test_credentials_local_save_status_and_preserve_env(tmp_path):
    path=tmp_path/'.env';path.write_text('ADMIN_TOKEN=existing-admin-value\nDATABASE_PATH=data/existing.db\n')
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'db'))
    headers={'Authorization':'Bearer '+cfg.admin_token}
    values={'telegram_token':'123456:private_bot_token','telegram_chat':'-123456'}
    with patch.dict(os.environ):
        with TestClient(create_app(cfg,Feed(),TG(),credentials_path=path)) as client:
            assert client.get('/api/credentials').status_code==200
            r=client.put('/api/credentials',headers=headers,json=values)
            assert r.status_code==200 and r.json()['telegram_configured']
            for endpoint in ['/api/credentials','/api/state']:
                text=client.get(endpoint,headers=headers).text
                assert all(secret not in text for secret in values.values())
            assert client.put('/api/credentials',headers=headers,json={'telegram_chat':'-987654'}).status_code==200
            assert cfg.telegram_token==values['telegram_token']
            cfg_state=client.app.state.service;cfg_state.analysis_active=True
            assert client.put('/api/credentials',headers=headers,json={'telegram_chat':'-987654'}).status_code==409
            cfg_state.analysis_active=False
            assert client.put('/api/credentials',headers=headers,json={'telegram_token':'','telegram_chat':''}).status_code==200
    saved=dotenv_values(path)
    assert saved['ADMIN_TOKEN']=='existing-admin-value' and saved['DATABASE_PATH']=='data/existing.db'
    assert saved['TELEGRAM_BOT_TOKEN']==''

def test_retired_gemini_is_not_configurable_and_existing_env_is_preserved(tmp_path):
    path=tmp_path/'.env';path.write_text('GEMINI_API_KEY=private-gemini-key\nGEMINI_MODEL=old-model\n')
    cfg=Config(database=str(tmp_path/'db'),gemini_api_key='private-gemini-key')
    with patch.dict(os.environ):
        with TestClient(create_app(cfg,Feed(),TG(),credentials_path=path)) as client:
            response=client.put('/api/credentials',json={'gemini_api_key':'another-private-key'})
            assert response.status_code==422
            for endpoint in ('/api/state','/api/credentials'):
                assert 'private-gemini-key' not in client.get(endpoint).text
            assert client.get('/api/credentials').json()['explanation_source']=='motor'
            response=client.put('/api/credentials',json={'telegram_token':'123:new','telegram_chat':'1'})
            assert response.status_code==200
            assert dotenv_values(path)['GEMINI_API_KEY']=='private-gemini-key'
            assert dotenv_values(path)['GEMINI_MODEL']=='old-model'

@pytest.mark.parametrize('body',[{'telegram_token':'bad_secret'},{'api_key':['private_secret']},{'api_key':'secret\nINJECT=yes'},{'admin_token':'secret'},['secret']])
def test_invalid_credentials_never_echo_or_persist(tmp_path,body):
    path=tmp_path/'.env';cfg=Config(admin_token='a'*32,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG(),credentials_path=path)) as client:
        response=client.put('/api/credentials',headers={'Authorization':'Bearer '+cfg.admin_token},json=body)
        assert response.status_code==422 and 'secret' not in response.text
    assert not path.exists()

def test_atomic_save_failure_keeps_previous_config(tmp_path):
    path=tmp_path/'.env';path.write_text('TELEGRAM_BOT_TOKEN=123:old\nTELEGRAM_CHAT_ID=123\n')
    cfg=Config(admin_token='a'*32,telegram_token='123:old',telegram_chat='123')
    with patch('app.credentials.os.replace',side_effect=OSError):
        with pytest.raises(OSError):update(path,cfg,{'telegram_token':'123:new'})
    assert cfg.telegram_token=='123:old' and path.read_text()=='TELEGRAM_BOT_TOKEN=123:old\nTELEGRAM_CHAT_ID=123\n'
    assert list(tmp_path.glob('.aurora-config-*'))==[]

def test_configuration_survives_process_environment_reset(tmp_path):
    import app.config as module
    path=tmp_path/'.env';path.write_text('ADMIN_TOKEN='+'a'*32+'\n')
    cfg=Config(admin_token='a'*32)
    with patch.dict(os.environ,{},clear=True):update(path,cfg,{'telegram_token':'123:persisted_key','telegram_chat':'123'})
    with patch.dict(os.environ,{},clear=True),patch.object(module,'ROOT',tmp_path):
        assert Config.load().telegram_token=='123:persisted_key'
