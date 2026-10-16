import asyncio
import time
from datetime import datetime
from app.quotes import Quotes
from app.market import Candle,DataError
from app.logging_setup import BRASILIA

class Provider:
    calls=0
    async def check_asset(self,asset):pass
    async def candles(self,*args):
        return [Candle(int(time.time()//60)*60,102,103,101,102)]
    def symbol(self,asset):return 'frx'+asset.replace('/','')
    async def request(self,payload,expected):
        self.calls+=1
        return {'candles':[dict(epoch=payload['end']//60*60,open=100,high=101,low=99,close=100)]}

def test_quote_percent_cache_and_all_assets():
    async def run():
        source=Provider();quotes=Quotes()
        rows=await asyncio.gather(*(quotes.get(source,a) for a in ['EUR/USD','GBP/USD','USD/JPY']))
        assert all(r['price']==102 and r['change_pct']==2 for r in rows)
        assert source.calls==3
        assert (await quotes.get(source,'EUR/USD'))['change_pct']==2
        assert source.calls==3
    asyncio.run(run())

def test_reference_failure_preserves_price_without_fake_percentage():
    class Missing(Provider):
        async def request(self,*args):return {'candles':[]}
    result=asyncio.run(Quotes().get(Missing(),'EUR/USD'))
    assert result['price']==102 and result['change_pct'] is None

def test_closed_market_label():
    class Closed(Provider):
        async def check_asset(self,asset):raise DataError('Fechado','market_closed')
    assert asyncio.run(Quotes().get(Closed(),'EUR/USD'))['status']=='closed'
