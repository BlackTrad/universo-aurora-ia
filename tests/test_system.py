"""Dados sintéticos exclusivos de teste; não são oportunidades nem backtest."""
import asyncio
import json
import time
from pathlib import Path
from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.strategies import scan_all as evaluate
from app.market import Candle,DataError,validate
from app.store import Store
from app.service import Service
from app.telegram import Telegram,caption,card
from app.main import create_app

BASE=1800000000//300*300

def fixture():
    return [Candle(**{**r,'t':r['t']+BASE}) for r in json.loads((Path(__file__).parent/'call_fixture.json').read_text())]

class Feed:
    name='Fonte de teste sintética'
    ready=True
    def __init__(self,rows=None,error=None): self.rows=rows or fixture();self.error=error;self.calls=0;self.settlement=[]
    async def candles(self,asset,interval=300,count=100):
        self.calls+=1
        if self.error: raise DataError(self.error)
        return self.rows if interval==300 else self.settlement

class TG:
    configured=True
    def __init__(self,status='enviado'):self.calls=0;self.status=status
    async def send(self,s):self.calls+=1;return {'status':self.status,'message_id':123}
    async def check(self):return {'status':'conectado'}

def setup(tmp_path,rows=None):
    config=Config(admin_token='a'*32,database=str(tmp_path/'db.sqlite'),strategy='candlestick_sr')
    now=[fixture()[-1].t+305]
    feed=Feed(rows);tg=TG();store=Store(config.database)
    return Service(config,store,feed,tg,clock=lambda:now[0]),now

async def drain(service):
    while service.tasks:
        await asyncio.gather(*list(service.tasks))


def test_no_setup():
    rows=[Candle(BASE+i*300,100,101,99,100) for i in range(50)]
    signal,reason=evaluate(rows)
    assert signal is None and reason

@pytest.mark.parametrize('mode',['stale','gap','duplicate','nan','bad_ohlc','future','incomplete','unsorted'])
def test_quality_blocks(mode):
    rows=fixture();now=rows[-1].t+305
    if mode=='stale':now+=400
    if mode=='gap':rows.pop(20)
    if mode=='duplicate':rows[20]=rows[19]
    if mode=='nan':rows[20]=Candle(rows[20].t,float('nan'),101,99,100)
    if mode=='bad_ohlc':rows[20]=Candle(rows[20].t,100,99,101,100)
    if mode=='future':rows.append(Candle(now+600,100,101,99,100))
    if mode=='incomplete':rows=rows[:20]
    if mode=='unsorted':rows.reverse()
    with pytest.raises(DataError):validate(rows,now)

def test_open_candle_is_excluded():
    rows=fixture();now=rows[-1].t+305
    rows.append(Candle(rows[-1].t+300,100,101,99,100))
    assert len(validate(rows,now))==50

def test_complete_flow_and_duplicate(tmp_path):
    async def run():
        s,now=setup(tmp_path)
        a=s.start(['EUR/USD'],300)
        assert a and s.start(['EUR/USD'],300) is None
        await drain(s)
        signal=s.store.signals()[0]
        assert signal['direction']=='CALL' and signal['telegram']['status']=='enviado'
        assert signal['entry_at']-now[0]>=60 and signal['entry_at']%60==0
        assert signal['expiry_at']-signal['entry_at']==300
        assert card(signal).startswith(b'\x89PNG')
        assert 'ID:' not in caption(signal) and signal['asset'] in caption(signal)
        s.start(['EUR/USD'],300);await drain(s)
        assert len(s.store.signals())==1 and s.telegram.calls==1
        assert 'duplicado' in s.store.analyses()[0]['checks'][0]['reason']
        second=Store(str(tmp_path/'db.sqlite'));assert second.signals()[0]['id']==signal['id']
        assert second.events(signal['id'])
    asyncio.run(run())

@pytest.mark.parametrize('mode',['no_setup','stale','provider_error'])
def test_negative_analysis(tmp_path,mode):
    async def run():
        s,now=setup(tmp_path)
        if mode=='no_setup':s.provider.rows=[Candle(BASE+i*300,100,101,99,100) for i in range(50)]
        if mode=='stale':now[0]+=500
        if mode=='provider_error':s.provider.error='Fonte indisponível'
        s.start(['EUR/USD'],300);await drain(s)
        a=s.store.analyses()[0]
        assert a['status']==('sem oportunidade' if mode=='no_setup' else 'erro') and a['checks'] and not s.store.signals()
    asyncio.run(run())

def test_expired_delivery_and_cancellation(tmp_path):
    async def run():
        s,now=setup(tmp_path);s.telegram.configured=False
        s.start(['EUR/USD'],300);await drain(s)
        signal=s.store.signals()[0]
        s.store.patch(signal['id'],{'telegram':{'status':'pendente'}})
        now[0]=signal['entry_at']
        count=s.telegram.calls
        await s.deliver(signal['id'])
        assert s.telegram.calls==count
        assert s.store.signal(signal['id'])['telegram']['status']=='expirado'
    asyncio.run(run())

@pytest.mark.parametrize('outcome',['WIN','LOSS','EMPATE','missing'])
def test_settlement(tmp_path,outcome):
    async def run():
        s,now=setup(tmp_path);s.start(['EUR/USD'],300);await drain(s)
        sig=s.store.signals()[0]
        now[0]=sig['entry_at'];await s.tick()
        assert s.store.signal(sig['id'])['state']=='em andamento'
        price={'WIN':101,'LOSS':99,'EMPATE':100,'missing':100}[outcome]
        s.provider.settlement=[] if outcome=='missing' else [Candle(sig['entry_at'],100,101,99,100),Candle(sig['expiry_at'],price,price+1,price-1,price)]
        now[0]=sig['expiry_at'];await s.tick()
        assert s.store.signal(sig['id'])['state']=='encerrado'
        now[0]=sig['expiry_at']+601;await s.tick();await drain(s)
        result=s.store.signal(sig['id'])
        assert result['state']=='encerrado'
        assert result['result']==('Resultado indisponível' if outcome=='missing' else outcome)
        assert result['result_kind']=='indicativo' and not result['broker_confirmed']
    asyncio.run(run())

def test_restart_inflight_never_resends(tmp_path):
    async def run():
        s,now=setup(tmp_path);s.start(['EUR/USD'],300);await drain(s)
        sig=s.store.signals()[0];s.store.patch(sig['id'],{'telegram':{'status':'enviando'}})
        s.store.recover(now[0]);await s.deliver(sig['id'])
        assert s.telegram.calls==1 and s.store.signal(sig['id'])['telegram']['status']=='incerto'
    asyncio.run(run())

@pytest.mark.parametrize('kind',['success','forbidden','timeout','server_error','rate_limit'])
def test_telegram_transport(kind):
    async def run():
        seen=[]
        def handler(request):
            seen.append(request)
            if kind=='timeout':raise httpx.ReadTimeout('simulated')
            if kind=='forbidden':return httpx.Response(403,json={'ok':False})
            if kind=='server_error':return httpx.Response(502,json={'ok':False})
            if kind=='rate_limit':return httpx.Response(429,json={'ok':False,'parameters':{'retry_after':3}})
            return httpx.Response(200,json={'ok':True,'result':{'message_id':88}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            cfg=Config(telegram_token='123:test',telegram_chat='123')
            s={'id':'test-id','asset':'EUR/USD','source':'TESTE SINTÉTICO','direction':'CALL','entry_at':BASE+300,'expiry_at':BASE+600,'duration_seconds':300,'timeframe':'M5','reasons':['Teste'],'score':85,'data_updated_at':BASE}
            result=await Telegram(cfg,client).send(s)
            assert result['status']=={'success':'enviado','forbidden':'falha','timeout':'incerto','server_error':'incerto','rate_limit':'repetir'}[kind]
            assert len(seen)==1
            assert seen[0].url.path.endswith('/sendPhoto')
            assert b'OPORTUNIDADE ENCONTRADA' not in seen[0].content and b'Ativo:' in seen[0].content
            assert b'\x89PNG\r\n\x1a\n' in seen[0].content
            assert b'test-id' not in seen[0].content
    asyncio.run(run())

def test_retries_are_bounded(tmp_path):
    async def run():
        s,now=setup(tmp_path)
        async def reject(signal):
            s.telegram.calls+=1
            return {'status':'repetir','retry_after':1}
        s.telegram.send=reject
        s.start(['EUR/USD'],300);await drain(s)
        assert s.telegram.calls==3
        assert s.store.signals()[0]['telegram']['status']=='falha'
    asyncio.run(run())

def test_failed_telegram_preserves_signal(tmp_path):
    async def run():
        s,now=setup(tmp_path);s.telegram.status='falha'
        s.start(['EUR/USD'],300);await drain(s)
        sig=s.store.signals()[0]
        assert sig['state']=='aguardando entrada' and sig['telegram']['status']=='falha'
    asyncio.run(run())

def test_api_security_and_full_flow(tmp_path):
    cfg=Config(admin_token='x'*32,database=str(tmp_path/'api.db'))
    rows=fixture();shift=int(time.time()//300)*300-rows[-1].t-300
    feed=Feed([Candle(c.t+shift,c.o,c.h,c.l,c.c) for c in rows])
    tg=TG()
    with TestClient(create_app(cfg,feed,tg,clock=lambda:feed.rows[-1].t+305)) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/state').status_code==200
        h={'Authorization':'Bearer '+cfg.admin_token}
        assert client.post('/api/analyze',json={'provider':'deriv','assets':['EUR/USD'],'timeframe':'M4'},headers=h).status_code==422
        assert client.post('/api/analyze',json={'provider':'deriv','assets':['FAKE']},headers=h).status_code==422
        assert client.post('/api/analyze',json={'provider':'deriv','assets':['EUR/USD']},headers={**h,'Origin':'https://evil.example'}).status_code==403
        assert client.post('/api/analyze',json={'provider':'deriv','assets':['EUR/USD']},headers=h).status_code==202
        for _ in range(50):
            snapshot=client.get('/api/state',headers=h).json()
            if not snapshot['busy']:break
            time.sleep(.02)
        assert not snapshot['signals'] and tg.calls==0
        assert snapshot['analyses'][0]['preview']
        assert client.post('/api/scan/start',json={'provider':'deriv','assets':['EUR/USD']},headers=h).status_code==202
        for _ in range(50):
            snapshot=client.get('/api/state',headers=h).json()
            if snapshot['signals']:break
            time.sleep(.02)
        assert snapshot['signals']
        sig=snapshot['signals'][0]
        assert client.get('/api/signals/'+sig['id']+'/card',headers=h).content.startswith(b'\x89PNG')
        assert client.get('/api/signals/'+sig['id'],headers=h).json()['events']
        assert cfg.admin_token not in json.dumps(snapshot)
        assert client.post('/api/scan/stop',headers=h).status_code==200
        assert client.put('/api/settings',json={'provider':'deriv','assets':['USD/JPY'],'duration_seconds':300},headers=h).status_code==200
        assert client.get('/api/state',headers=h).json()['settings']['duration_seconds']==300

@pytest.mark.parametrize('bad',[{'provider':'mock'},{'provider':'unsupported'},{'assets':['EURUSD-OTC']},{'min_lead':0},{'telegram_token':'123:test'}])
def test_config_rejects_bad(bad):
    values={'admin_token':'a'*32};values.update(bad)
    with pytest.raises(ValueError):Config(**values).validate()


def test_same_source_identity_required_for_settlement(tmp_path):
    async def run():
        s,now=setup(tmp_path);s.provider.identity='broker-A'
        s.start(['EUR/USD'],300);await drain(s)
        sig=s.store.signals()[0];s.provider.identity='broker-B'
        s.provider.settlement=[Candle(sig['entry_at'],100,101,99,100),Candle(sig['expiry_at'],101,102,100,101)]
        now[0]=sig['expiry_at']+601;await s.tick();await drain(s)
        assert s.store.signal(sig['id'])['result']=='Resultado indisponível'
    asyncio.run(run())

def test_structure_events_are_causal():
    from app.manual_price_action import pivots
    rows=fixture()
    complete=pivots(rows)
    for count in range(4,len(rows)+1):
        cutoff=rows[count-1].t+300
        assert pivots(rows[:count])==[p for p in complete if p['confirmed_at']<=cutoff]
