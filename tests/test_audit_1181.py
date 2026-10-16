import asyncio
from unittest.mock import patch
from tests.test_system import setup,drain,fixture


def test_running_normally_does_not_gain_restart_recovery_window(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        service.start(['EUR/USD'],300);await drain(service)
        sig=service.store.signals()[0]
        async def absent(*args):return []
        service.provider.settlement_candles=absent
        clock[0]=sig['expiry_at']+65;await service.settle(sig)
        clock[0]=sig['expiry_at']+600;await service.settle(service.store.signal(sig['id']))
        result=service.store.signal(sig['id'])
        assert not result['settlement_pending'] and result['result']=='Resultado indisponível'
        assert 'settlement_retry_until' not in result
    asyncio.run(run())


def test_recovery_deadline_survives_another_restart(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        service.start(['EUR/USD'],300);await drain(service)
        sig=service.store.signals()[0];clock[0]=sig['expiry_at']+7200
        service.store.recover(clock[0]);first=service.store.signal(sig['id'])
        assert first['settlement_retry_until']==clock[0]+600
        clock[0]+=300;service.store.recover(clock[0])
        assert service.store.signal(sig['id'])['settlement_retry_until']==first['settlement_retry_until']
    asyncio.run(run())


def test_delayed_candidates_have_explicit_diagnostic_and_short_retry(tmp_path):
    async def run():
        service,clock=setup(tmp_path);original=service.provider.candles
        async def delayed(asset,interval=300,count=100):
            clock[0]+=15
            return await original(asset,interval,count)
        service.provider.candles=delayed
        c=dict(direction='CALL',score=90,setup_candle=fixture()[-1].t,reasons=['synthetic'])
        with patch('app.service.evaluate',side_effect=[(c,''),(None,'Sem setup'),(None,'Sem setup'),(None,'Sem setup')]):
            service.start(['EUR/USD','GBP/USD','USD/JPY','AUD/USD'],300);await drain(service)
        a=service.store.analyses()[0]
        assert a['status']=='descartada por atraso' and a['discarded_assets']==1
        assert a['retry_after_seconds']==15 and not service.store.signals()
        # Check the monitor consumes the retry instead of waiting for the next candle.
        scan={'active':True,'id':'test','assets':['EUR/USD'],'duration_seconds':300,'timeframe':'M5','cycles':0}
        async def done():pass
        def start(*args,**kwargs):
            service.current_analysis_task=asyncio.create_task(done());return a
        async def stop_wait(seconds):scan['active']=False
        before=clock[0]
        with patch.object(service,'start',start),patch('app.service.asyncio.sleep',stop_wait):
            await service._scan_loop(scan)
        assert '15 segundos' in scan['message'] and clock[0]==before
    asyncio.run(run())
