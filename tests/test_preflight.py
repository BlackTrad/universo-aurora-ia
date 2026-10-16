import os
from unittest.mock import patch
import pytest
from app import config, preflight
from app.logging_setup import configure

@pytest.mark.parametrize('changes,field',[
    ({'DATA_PROVIDER':'PRIVATE_VALUE'},'Fonte'),
    ({'STRATEGY':'PRIVATE_VALUE'},'Estratégia'),
    ({'CONFIRM_HTF':'true'},'CONFIRM_HTF'),
    ({'MIN_LEAD_SECONDS':'PRIVATE_VALUE'},'MIN_LEAD_SECONDS'),
    ({'MAX_DATA_AGE_SECONDS':'PRIVATE_VALUE'},'MAX_DATA_AGE_SECONDS'),
    ({'TELEGRAM_BOT_TOKEN':'PRIVATE_VALUE','TELEGRAM_CHAT_ID':'123'},'TELEGRAM_BOT_TOKEN'),
    ({'TELEGRAM_BOT_TOKEN':'123:PRIVATE_VALUE'},'TELEGRAM_CHAT_ID'),
])
def test_exact_safe_error(tmp_path,capsys,changes,field):
    with patch.dict(os.environ,{'ADMIN_TOKEN':'a'*32,**changes},clear=True),patch.object(config,'ROOT',tmp_path),patch.object(preflight,'ROOT',tmp_path),patch.object(preflight,'configure',side_effect=lambda:configure(tmp_path/'logs')):
        assert preflight.main()==2
    output=capsys.readouterr().out
    assert field in output and 'PRIVATE_VALUE' not in output
    assert 'PRIVATE_VALUE' not in (tmp_path/'logs/aurora.log').read_text()

def test_blank_api_credentials_allow_panel_and_windows_bom(tmp_path):
    (tmp_path/'.env').write_text('ADMIN_TOKEN='+'a'*32+'\nTELEGRAM_BOT_TOKEN=\nTELEGRAM_CHAT_ID=\nTWELVEDATA_API_KEY=\n',encoding='utf-8-sig')
    with patch.dict(os.environ,{},clear=True),patch.object(config,'ROOT',tmp_path),patch.object(preflight,'configure',side_effect=lambda:configure(tmp_path/'logs')):
        assert preflight.main()==0

def test_unexpected_error_is_not_echoed(tmp_path,capsys):
    with patch.object(preflight.Config,'load',side_effect=RuntimeError('PRIVATE_VALUE')),patch.object(preflight,'configure',side_effect=lambda:configure(tmp_path/'logs')):
        assert preflight.main()==3
    assert 'PRIVATE_VALUE' not in capsys.readouterr().out
    assert 'PRIVATE_VALUE' not in (tmp_path/'logs/aurora.log').read_text()
