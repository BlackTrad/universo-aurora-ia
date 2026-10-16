import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.telegram import Telegram
from tests.test_system import Feed,TG

@pytest.mark.parametrize('mode,expected',[('ok','conectado'),('timeout','incerto'),('broken','incerto'),('500','incerto'),('401','falha'),('403','falha'),('400','falha'),('429','falha')])
def test_check_sends_one_explicit_message(mode,expected):
    calls=[]
    async def run():
        def response(request):
            calls.append(request)
            assert request.url.path.endswith('/sendMessage')
            payload=json.loads(request.content)
            assert payload['chat_id']=='-123' and 'TESTE DE CONEXÃO' in payload['text']
            assert 'Não é um sinal' in payload['text'] and 'Horário de Brasília' in payload['text']
            assert 'private_token' not in payload['text']
            if mode=='timeout':raise httpx.ReadTimeout('private_token')
            if mode=='broken':return httpx.Response(200,text='private_token')
            if mode=='ok':return httpx.Response(200,json={'ok':True,'result':{'message_id':42}})
            return httpx.Response(int(mode),json={'ok':False,'error_code':int(mode),'description':'private_token'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
            result=await Telegram(Config(telegram_token='123:private_token',telegram_chat='-123'),client).check()
        assert result['status']==expected and 'private_token' not in str(result)
        assert len(calls)==1
    asyncio.run(run())

def test_unconfigured_sends_nothing():
    async def run():
        def fail(request):raise AssertionError('Must not contact Telegram')
        async with httpx.AsyncClient(transport=httpx.MockTransport(fail)) as client:
            assert (await Telegram(Config(),client).check())['status']=='não configurado'
    asyncio.run(run())

def test_api_repeated_click_and_busy_credentials(tmp_path):
    class Fake(TG):
        async def check(self):self.calls+=1;return {'status':'conectado','detail':'Synthetic test'}
    fake=Fake();cfg=Config(database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),fake)) as client:
        assert client.post('/api/telegram/check').status_code==200
        assert client.post('/api/telegram/check').status_code==429
        assert fake.calls==1
        service=client.app.state.service
        client.portal.call(service.telegram_check_lock.acquire)
        try:
            assert client.post('/api/telegram/check').status_code==409
            assert client.put('/api/credentials',json={'api_key':'new_key'}).status_code==409
        finally:client.portal.call(service.telegram_check_lock.release)
