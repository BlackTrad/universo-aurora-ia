"""1.2 regressions: controlled data are not live integration evidence."""
import asyncio
import copy
import math
import time
from unittest.mock import patch
import httpx
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.deriv import DerivProvider
from app.market import Candle, DataError, validate
from app.telegram import Telegram, caption, card, text_units
from tests.test_system import setup, fixture, drain, Feed, TG
from app.main import create_app

@pytest.mark.parametrize('stamp',[float('nan'),float('inf'),True,'1800000000',-300,1800000000.5])
def test_invalid_timestamp_is_data_error(stamp):
    rows=fixture();r=rows[-1];rows[-1]=Candle(stamp,r.o,r.h,r.l,r.c)
    with pytest.raises(DataError):validate(rows,r.t+305)

def test_boolean_price_rejected():
    rows=fixture();r=rows[-1];rows[-1]=Candle(r.t,True,r.h,r.l,r.c)
    with pytest.raises(DataError):validate(rows,r.t+305)

def test_new_boundary_during_evaluation_discards_signal(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        from app.service import evaluate as original
        def delayed(rows):
            result=original(rows);now[0]+=300;return result
        with patch('app.service.evaluate',side_effect=delayed):
            service.start(['EUR/USD'],300);await drain(service)
        assert not service.store.signals()
        analysis=service.store.analyses()[0]
        assert analysis['status']=='erro' and analysis['checks'][0]['code']=='candle_changed'
    asyncio.run(run())

def test_partial_analysis_is_explicit(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        rows=[Candle(c.t,100,101,99,100) for c in fixture()]
        async def candles(asset,*args):
            if asset=='GBP/USD':raise DataError('Falha de conexão','connection')
            return rows
        service.provider.candles=candles
        service.start(['EUR/USD','GBP/USD'],300);await drain(service)
        analysis=service.store.analyses()[0]
        assert analysis['partial'] and analysis['failed_assets']==1 and analysis['evaluated_assets']==1
        assert analysis['status']=='sem oportunidade' and 'parcial' in analysis['message']
    asyncio.run(run())

def test_wallclock_jump_does_not_unlock_active_analysis(tmp_path):
    async def run():
        service,now=setup(tmp_path)
        entered=asyncio.Event();release=asyncio.Event()
        async def slow(*args):entered.set();await release.wait();raise DataError('Offline')
        service.provider.candles=slow
        service.start(['EUR/USD'],300);await entered.wait();now[0]+=1000
        assert service.start(['EUR/USD'],300) is None
        with pytest.raises(RuntimeError):service.select_provider('deriv')
        assert service.snapshot()['busy']
        release.set();await drain(service);assert not service.snapshot()['busy']
    asyncio.run(run())

def test_cancelled_analysis_releases_memory_and_db_lock(tmp_path):
    async def run():
        service,_=setup(tmp_path);entered=asyncio.Event()
        async def slow(*args):entered.set();await asyncio.Event().wait()
        service.provider.candles=slow
        service.start(['EUR/USD'],300);await entered.wait()
        tasks=list(service.tasks)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        assert not service.analysis_active and not service.store.busy(service.clock())
        assert service.store.analyses()[0]['status']=='interrompida'
    asyncio.run(run())

def test_duplicate_delivery_guard_during_retry_wait(tmp_path):
    async def run():
        service,_=setup(tmp_path)
        service.start(['EUR/USD'],300);await drain(service)
        sid=service.store.signals()[0]['id'];service.store.patch(sid,{'telegram':{'status':'pendente'}})
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def send(signal):
            calls.append(1);entered.set();await release.wait();return {'status':'enviado'}
        service.telegram.send=send
        task=asyncio.create_task(service.deliver(sid));await entered.wait()
        # Even if a persisted retry status is observed, another task cannot take ownership.
        service.store.patch(sid,{'telegram':{'status':'repetir','retry_after':1}})
        await service.deliver(sid);release.set();await task
        assert len(calls)==1 and not service.delivering
    asyncio.run(run())

def test_history_filters_pagination_and_legacy_evidence(tmp_path):
    async def seed():
        service,_=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service);return service
    service=asyncio.run(seed());original=service.store.signals()[0]
    for i in range(105):
        signal=copy.deepcopy(original);signal.update(id=f'archive-{i}',created_at=original['created_at']+i+1,
            entry_at=original['entry_at']+i*300,source_key='deriv' if i%2 else 'twelvedata',direction='PUT',state='encerrado',settlement_pending=False)
        signal.pop('movement_id',None)
        service.store.add_signal(signal,signal['id'])
    first=service.store.history(page=1,page_size=20);second=service.store.history(page=2,page_size=20)
    assert first['total']==106 and len(first['items'])==20
    assert not ({s['id'] for s in first['items']}&{s['id'] for s in second['items']})
    assert all('evidence' not in s for s in first['items'])
    assert service.store.current_signal()['id']==original['id']  # outside recent 100
    filtered=service.store.history(source='deriv',direction='PUT',state='encerrado',start=original['entry_at'],end=original['entry_at']+600)
    assert filtered['total']==1
    assert service.store.history(asset="EUR/USD' OR 1=1 --")['total']==0
    assert service.store.signal(original['id'])['evidence']==original['evidence']
    service.store.db.close()

@pytest.mark.parametrize('query',['page=0','page_size=100','direction=BUY','source=mock','start=5&end=4','start=nan','end=inf'])
def test_history_endpoint_validation(tmp_path,query):
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as c:
        assert c.get('/api/history').status_code==200
        assert c.get('/api/history?'+query,headers={'Authorization':'Bearer '+cfg.admin_token}).status_code==422

def test_unordered_settlement_rejected(tmp_path):
    async def run():
        service,now=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service)
        signal=service.store.signals()[0];now[0]=signal['expiry_at']+601
        service.provider.settlement=[Candle(signal['expiry_at'],101,102,100,101),Candle(signal['entry_at'],100,101,99,100)]
        await service.settle(signal)
        result=service.store.signal(signal['id']);assert result['result']=='Resultado indisponível' and not result['settlement_pending']
    asyncio.run(run())

def test_many_settlements_do_not_create_unbounded_tasks(tmp_path):
    async def run():
        service,now=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service)
        first=service.store.signals()[0];now[0]=first['expiry_at']+70
        for i in range(15):
            s=copy.deepcopy(first);s.update(id=f'pending-{i}',movement_id=None)
            service.store.add_signal(s,s['id'])
        gate=asyncio.Event()
        async def slow(*args):await gate.wait();raise DataError('Offline')
        service.provider.candles=slow
        await service.tick()
        assert len(service.settling)==2
        assert all(s['state']=='encerrado' for s in service.store.active())
        gate.set();await drain(service)
    asyncio.run(run())

def test_telegram_long_unicode_and_render_failure(tmp_path):
    async def run():
        service,_=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service)
        s=service.store.signals()[0];s['reasons']=['Teste < > & 🟢 '*500]
        legend=caption(s)
        assert text_units(legend)<=1024 and 'ID:' not in legend and 'SEM MARTINGALE' in legend
        sent=[]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:sent.append(r))) as client:
            tg=Telegram(Config(telegram_token='123:test',telegram_chat='1'),client)
            with patch('app.telegram.caption',side_effect=ValueError('private internal error')):
                result=await tg.send(s)
            assert result['status']=='falha' and not sent and 'private' not in str(result)
    asyncio.run(run())

def test_telegram_deadline_rechecked_after_image(tmp_path):
    async def run():
        service,_=setup(tmp_path);service.start(['EUR/USD'],300);await drain(service);s=service.store.signals()[0]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:pytest.fail('Late request'))) as client:
            tg=Telegram(Config(telegram_token='123:test',telegram_chat='1'),client)
            with patch('app.telegram.time.time',return_value=s['entry_at']-5):result=await tg.send(s)
            assert result['status']=='expirado'
    asyncio.run(run())

def test_contract_cache_expires_and_failure_never_reuses_old_data():
    async def run():
        p=DerivProvider(Config());calls=[];clock=[100]
        row={'contract_type':'CALL','underlying_symbol':'frxEURUSD','barriers':0,'min_contract_duration':'1m','max_contract_duration':'10m'}
        async def request(*args):
            calls.append(1)
            if len(calls)>1:raise DataError('Offline')
            return {'contracts_for':{'available':[row]}}
        p.request=request
        with patch('app.deriv.time.monotonic',side_effect=lambda:clock[0]):
            await p.supports('EUR/USD','CALL',300);clock[0]+=29;await p.supports('EUR/USD','CALL',300)
            assert len(calls)==1
            clock[0]+=2
            with pytest.raises(DataError):await p.supports('EUR/USD','CALL',300)
            assert not p.contract_cache
    asyncio.run(run())


@pytest.mark.parametrize('path',['/api/analyze','/api/settings'])
def test_source_is_required_even_for_old_clients(tmp_path,path):
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as c:
        method=c.post if path.endswith('analyze') else c.put
        assert method(path,headers={'Authorization':'Bearer '+cfg.admin_token},json={'assets':['EUR/USD']}).status_code==422


def test_history_dates_are_brasilia_and_end_inclusive(tmp_path):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    cfg=Config(admin_token='a'*32,database=str(tmp_path/'db'))
    with TestClient(create_app(cfg,Feed(),TG())) as c:
        store=c.app.state.service.store
        stamps=[datetime(2026,9,22,hour,tzinfo=ZoneInfo('America/Sao_Paulo')).timestamp() for hour in (0,23)]
        for i,stamp in enumerate(stamps):store.add_signal({'id':str(i),'created_at':stamp,'entry_at':stamp},'f'+str(i))
        r=c.get('/api/history?start_date=2026-09-22&end_date=2026-09-22',headers={'Authorization':'Bearer '+cfg.admin_token})
        assert r.status_code==200 and r.json()['total']==2
        assert c.get('/api/history?start=10&start_date=2026-09-22',headers={'Authorization':'Bearer '+cfg.admin_token}).status_code==422

def test_backup_preserves_evidence_and_refuses_overwrite(tmp_path):
    from scripts.backup_db import backup
    from app.store import Store
    service,_=setup(tmp_path)
    service.store.add_signal({'id':'original','created_at':1,'state':'cancelado','evidence':{'original':True}},'unique')
    target=tmp_path/'backup.db'
    backup(service.config.database,target)
    copied=Store(str(target))
    assert copied.signal('original')['evidence']=={'original':True}
    assert len(copied.events('original'))==1
    copied.db.close()
    with pytest.raises(FileExistsError):backup(service.config.database,target)
    assert service.store.signal('original')['evidence']=={'original':True}
