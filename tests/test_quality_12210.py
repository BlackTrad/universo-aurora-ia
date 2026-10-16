"""Synthetic safety and causal replay checks, not performance evidence."""
import asyncio
import json
from unittest.mock import patch
import httpx
import pytest
from app.market import Candle, CandleBatch, DataError
from app.strategies import scan_all, STRATEGIES
from app.strategies.quality import selection_guard, entry_guard
from app.learning import profiles, choose_tie
from app.telegram import Telegram
from app.config import Config
from tests.test_learning import records, CONTEXT, SCOPE
from tests.test_system import setup, drain, fixture

def candidate(key='cci', direction='CALL'):
    return dict(strategy_resolved=key,direction=direction,score=95,setup_candle=120,reasons=[])




def entry_signal(entry=1200, direction='CALL'):
    return dict(id='check',asset='EUR/USD',direction=direction,entry_at=entry,expiry_at=entry+300,
                created_at=entry-100,timeframe='M5',duration_seconds=300,source_key='deriv',source_identity='synthetic',
                state='aguardando entrada',result='Resultado indisponível',settlement_pending=True,
                telegram={'status':'enviado'},evidence={'candles':[dict(c=100)]},
                entry_validation={'status':'pendente','scheduled_at':entry-45,'atr':1})

def recent(now,price=100):
    last=int(now//60)*60-60
    return [Candle(last-(2-i)*60,price,price+.1,price-.1,price) for i in range(3)]

@pytest.mark.parametrize('direction,price,rejected',[('CALL',99.6,True),('PUT',100.4,True),('CALL',100.8,True),('PUT',99.2,True),('CALL',100.1,False),('PUT',99.9,False)])
def test_guard_both_directions_and_chasing(direction,price,rejected):
    s=entry_signal(direction=direction)
    audit,reason=entry_guard(s,recent(1155,price),1155)
    assert bool(reason)==rejected and audit['candle_at']==1080

def test_guard_ignores_unfinished_candle_but_rejects_stale_gaps_and_deadline():
    s=entry_signal();rows=recent(1155)
    assert not entry_guard(s,rows+[Candle(1140,100,110,90,90)],1155)[1]
    for values,now in ((rows[:-1],1155),(rows[::2],1155),(rows,1185)):
        with pytest.raises(DataError):entry_guard(s,values,now)

@pytest.mark.parametrize('mode',['confirm','adverse','source','stale','timeout','late'])
def test_persisted_revalidation_and_cancellation_notify_once_without_loss(tmp_path,mode):
    async def run():
        service,clock=setup(tmp_path);entry=(int(clock[0]//60)+3)*60
        s=entry_signal(entry);s['source_identity']=service.provider.name
        service.store.add_signal(s,'new-check')
        clock[0]=entry-(15 if mode=='late' else 45)
        calls=[]
        async def candles(*args):
            if mode=='timeout':raise asyncio.TimeoutError
            rows=recent(clock[0],99.5 if mode=='adverse' else 100)
            if mode=='stale':rows=rows[:-1]
            return CandleBatch(rows,'wrong' if mode=='source' else service.provider.name)
        async def send_result(signal,totals):
            calls.append((signal,totals));return {'status':'enviado','message_id':2}
        service.provider.candles=candles;service.telegram.send_result=send_result
        await service.tick();await drain(service)
        saved=service.store.signal(s['id'])
        if mode=='confirm':
            assert saved['entry_validation']['status']=='confirmada' and not calls
            clock[0]=entry;await service.tick()
            assert service.store.signal(s['id'])['state']=='em andamento'
        else:
            assert saved['state']=='cancelado' and saved['result']=='CANCELADO'
            assert not saved['settlement_pending'] and saved['estimated_pnl_brl'] is None
            assert len(calls)==1 and calls[0][1]['losses']==0
            assert service.store.apex_limits(clock[0])['used']==1
            assert service.store.apex_limits(clock[0])['active']==0
            assert not list(service.store.active())
            await service.tick();await drain(service);assert len(calls)==1
        assert not service.revalidating
    asyncio.run(run())

def test_restart_does_not_let_unchecked_signal_enter(tmp_path):
    async def run():
        service,clock=setup(tmp_path);s=entry_signal((int(clock[0]//60)+3)*60)
        service.store.add_signal(s,'recovery');clock[0]=s['entry_at']+1
        service.store.recover(clock[0]);await service.tick();await drain(service)
        assert service.store.signal(s['id'])['state']=='cancelado'
    asyncio.run(run())

def test_slow_revalidation_cannot_confirm_after_deadline(tmp_path):
    async def run():
        service,clock=setup(tmp_path);s=entry_signal((int(clock[0]//60)+3)*60)
        s['source_identity']=service.provider.name
        service.store.add_signal(s,'slow-check');clock[0]=s['entry_at']-45
        async def candles(*args):
            clock[0]=s['entry_at']-10
            return CandleBatch(recent(clock[0]),service.provider.name)
        service.provider.candles=candles
        await service.tick();await drain(service)
        assert service.store.signal(s['id'])['entry_validation']['status']=='rejeitada'
        assert service.store.signal(s['id'])['state']=='cancelado'
    asyncio.run(run())

def test_first_delivery_cannot_race_pre_entry_validation_window(tmp_path):
    async def run():
        service,clock=setup(tmp_path);s=entry_signal((int(clock[0]//60)+3)*60)
        s['telegram']={'status':'pendente'};service.store.add_signal(s,'late-send')
        clock[0]=s['entry_at']-59
        await service.deliver(s['id'])
        assert service.telegram.calls==0
        assert service.store.signal(s['id'])['telegram']['status']=='expirado'
    asyncio.run(run())


def test_learning_negative_train_and_validation_veto_but_small_sample_cannot():
    bad=records(wins=0)
    learned=profiles(bad,100000)
    assert next(iter(learned.values()))['blocked']
    selected=choose_tie([candidate(),candidate('fibonacci')],learned,CONTEXT,scope=SCOPE)
    assert selected['strategy_resolved']=='fibonacci' and selected['learning']['applied']
    assert choose_tie([candidate()],learned,CONTEXT,scope=SCOPE)['learning']['blocked']
    assert not next(iter(profiles(bad[:6],100000).values()))['blocked']
    assert not next(iter(profiles(records(wins=60),100000).values()))['blocked']


def test_cancellation_telegram_keeps_totals_without_money_or_win_loss():
    async def run():
        sent=[]
        def handler(req):
            sent.append(json.loads(req.content)['text']);return httpx.Response(200,json={'ok':True,'result':{'message_id':1}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            s=entry_signal();s.update(state='cancelado',result='CANCELADO',estimated_pnl_brl=8,cancellation_reason='Preço contra o setup.')
            await Telegram(Config(telegram_token='123:test',telegram_chat='1'),client).send_result(s,{'wins':0,'losses':0,'ties':0,'win_rate_pct':None})
            assert 'ENTRADA CANCELADA' in sent[0] and 'Não entrar:' in sent[0]
            assert 'R$' not in sent[0] and 'Vitórias: 0 · Derrotas: 0' in sent[0]
    asyncio.run(run())
