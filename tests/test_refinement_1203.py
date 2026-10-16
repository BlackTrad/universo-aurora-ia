import asyncio,time
from app.market import DataError
from app.quotes import Quotes
from tests.test_quotes import Provider
from tests.test_system import setup,drain

def test_signal_does_not_require_broker_contract(tmp_path):
    async def run():
        service,_=setup(tmp_path)
        async def supports(*args):raise AssertionError('Signal analysis must not request broker contracts')
        service.provider.supports=supports
        analysis=service.start(['EUR/USD'],300)
        await drain(service)
        assert analysis['failed_assets']==0 and analysis['blocked_assets']==0
        assert service.feed['status']=='disponível'
        assert len(service.store.signals())==1
        assert service.telegram.calls==1
        assert service.store.signals()[0]['broker_confirmed'] is False
    asyncio.run(run())

def test_reference_chooses_closed_candle_before_midnight():
    class Source(Provider):
        async def request(self,payload,expected):
            assert payload['end']%60==0 and payload['count']==2
            return {'candles':[dict(epoch=payload['end'],open=100,high=101,low=99,close=100),dict(epoch=payload['end']+60,open=110,high=111,low=109,close=110)]}
    result=asyncio.run(Quotes().get(Source(),'EUR/USD'))
    assert result['change_pct']==2

def test_missing_reference_backs_off_but_updates_price():
    async def run():
        class Source(Provider):
            async def request(self,*args):self.calls+=1;return {'candles':[]}
        source=Source();quotes=Quotes()
        for _ in range(3):
            quotes.cache.clear()
            result=await quotes.get(source,'EUR/USD')
            assert result['price']==102 and result['change_pct'] is None
        assert source.calls==1
    asyncio.run(run())
