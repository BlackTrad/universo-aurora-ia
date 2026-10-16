"""Regression cases reproduced during the 1.17.4 audit; synthetic quotes only."""
import asyncio
from unittest.mock import patch
from app.store import Store
from app.deriv import DerivProvider
from app.config import Config
from app.market import Candle,DataError
from app.learning import profiles,choose_tie
from app.strategies import STRATEGIES,scan_all
from tests.test_learning import records,CONTEXT,SCOPE
from tests.test_system import setup,drain


def test_loss_pause_starts_when_both_consecutive_losses_are_known():
    store=Store(':memory:')
    base=dict(source_key='deriv',asset='EUR/USD',strategy='cci',direction='CALL',state='encerrado',result='LOSS')
    for row in [dict(id='older',created_at=2200,entry_at=2280,expiry_at=2340,settled_at=3500),
                dict(id='newer',created_at=2250,entry_at=2340,expiry_at=2400,settled_at=2465)]:
        store.add_signal({**base,**row},row['id'])
    assert store.apex_limits(3500)['pause_until']==4400
    assert store.apex_limits(4399)['pause_until']==4400
    accepted,reason=store.add_apex_signal({'id':'blocked','created_at':3500,'entry_at':3600},'blocked')
    assert not accepted and 'pausa' in reason
    assert store.apex_limits(4400)['pause_until']==0
    store.db.close()


def test_metrics_separate_rules_pipeline_and_unversioned_records():
    store=Store(':memory:')
    for i,(rules,pipeline) in enumerate([('1.16','old'),('1.17','old'),('1.17','new'),(None,None)]):
        store.add_signal(dict(id=str(i),created_at=i,asset='EUR/USD',source_key='deriv',strategy='cci',direction='CALL',timeframe='M1',duration_seconds=60,state='encerrado',result='WIN',evidence=dict(rules_version=rules,pipeline_version=pipeline)),str(i))
    metrics=store.apex_metrics()
    assert len(metrics)==4 and all(m['signals']==1 for m in metrics)
    assert any(m['rules_version']=='não informada' for m in metrics)
    store.db.close()


def test_historical_request_targets_expiration_instead_of_latest():
    async def run():
        provider=DerivProvider(Config());calls=[]
        async def request(payload,expected):
            calls.append(payload)
            return {'echo_req':payload,'candles':[dict(epoch=t,open=100,high=101,low=99,close=100) for t in (600,900)]}
        provider.request=request
        rows=await provider.settlement_candles('EUR/USD',600,900)
        assert calls[0]['end']==959 and calls[0]['count']==7 and calls[0]['granularity']==60
        assert [c.t for c in rows]==[600,900] and rows.source_identity==provider.identity
    asyncio.run(run())


def test_old_pending_signal_recovers_exact_quotes_and_preserves_retry_deadline(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        service.start(['EUR/USD'],300);await drain(service)
        signal=service.store.signals()[0]
        clock[0]=signal['expiry_at']+7200
        service.store.recover(clock[0]);signal=service.store.signal(signal['id'])
        calls=[]
        async def historic(asset,entry,expiry):
            calls.append((entry,expiry))
            if len(calls)==1:raise DataError('Temporary failure')
            return [Candle(entry,100,101,99,100),Candle(expiry,101,102,100,101)]
        service.provider.settlement_candles=historic
        await service.settle(signal)
        pending=service.store.signal(signal['id'])
        assert pending['settlement_pending'] and pending['settlement_retry_until']==clock[0]+600
        clock[0]+=60
        await service.settle(pending)
        resolved=service.store.signal(signal['id'])
        assert resolved['result']=='WIN' and not resolved['settlement_pending']
        assert calls[-1]==(signal['entry_at'],signal['expiry_at'])
        assert resolved['settlement_retry_until']==pending['settlement_retry_until']
    asyncio.run(run())


def test_missing_historical_quotes_expire_without_inventing_result_or_extending_forever(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        service.start(['EUR/USD'],300);await drain(service)
        signal=service.store.signals()[0];clock[0]=signal['expiry_at']+7200
        service.store.recover(clock[0]);signal=service.store.signal(signal['id'])
        async def historic(*args):return []
        service.provider.settlement_candles=historic
        await service.settle(signal)
        signal=service.store.signal(signal['id']);deadline=signal['settlement_retry_until']
        clock[0]=deadline
        await service.settle(signal)
        resolved=service.store.signal(signal['id'])
        assert not resolved['settlement_pending'] and resolved['result']=='Resultado indisponível'
        assert resolved['settlement_retry_until']==deadline
    asyncio.run(run())


