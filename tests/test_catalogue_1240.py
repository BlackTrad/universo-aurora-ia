"""Provider/source separation and Brasília day boundaries with synthetic quotes."""
from datetime import datetime,timezone
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.market import Candle,CandleBatch,DataError
from tests.test_system import TG
from tests.test_chart import Source

class Crypto(Source):
    name='Binance';identity='binance:test';assets=['BTC/USDT','ETH/USDT'];timeframes=['M1','M5']
    async def reference_candle(self,asset,midnight):return Candle(midnight-60,100,101,99,100)
    async def candles(self,asset,interval,count):
        rows=await super().candles(asset,interval,count)
        return CandleBatch(rows,self.identity)
class Coinbase(Crypto):
    name='Coinbase';identity='coinbase:test';assets=['BTC/USD','ETH/USD']


def test_catalogue_search_pagination_identity_and_readonly_source(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Source(),TG())) as c:
        service=c.app.state.service
        service.providers.update(binance=Crypto(),coinbase=Coinbase())
        first=c.get('/api/instruments?page_size=3').json()
        assert {r['source_key'] for r in first['items']}=={'deriv','binance','coinbase'}
        assert len({r['id'] for r in first['items']})==3 and first['total']==11
        btc=c.get('/api/instruments?q=Bitcoin&market=crypto').json()['items']
        assert {r['id'] for r in btc}=={'binance:BTC/USDT','coinbase:BTC/USD'}
        assert not any(r['analysis_enabled'] for r in btc)
        assert c.get('/api/instruments?source=binance').json()['total']==2
        assert c.get('/api/instruments?page=2&page_size=3').json()['items']!=first['items']
        a=c.get('/api/chart?asset=BTC/USDT&source=binance&timeframe=M1').json()
        b=c.get('/api/chart?asset=BTC/USD&source=coinbase&timeframe=M1').json()
        assert a['source_key']=='binance' and a['source_identity']=='binance:test'
        assert b['source_key']=='coinbase' and b['asset']=='BTC/USD'
        assert c.get('/api/quote?asset=BTC/USD&source=coinbase').json()['source_key']=='coinbase'
        assert c.get('/api/state').json()['provider']=='deriv'
        assert c.get('/api/chart?asset=BTC/USD&source=binance').status_code==422
        assert c.get('/api/chart?asset=BTC/USDT&source=binance&timeframe=M3').status_code==422
        assert not service.store.signals()


def test_closed_quote_without_prices_keeps_closed_status(tmp_path):
    class Closed(Source):
        async def check_asset(self,a):raise DataError('Closed','market_closed')
        async def candles(self,*args):raise DataError('No quote')
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Closed(),TG())) as c:
        data=c.get('/api/quote?asset=EUR/USD&source=deriv').json()
        assert data['status']=='closed' and data['price'] is None and data['change_pct'] is None


def test_history_days_brasilia_boundary_no_deletion_and_full_filters(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Source(),TG())) as c:
        store=c.app.state.service.store
        midnight=int(datetime(2026,10,3,3,tzinfo=timezone.utc).timestamp())
        for i,entry in enumerate([midnight-1,midnight,midnight+1]):
            store.add_signal({'id':str(i),'created_at':entry-120,'entry_at':entry,'expiry_at':entry+60,
                'asset':'EUR/USD','source_key':'deriv','strategy_resolved':'cci' if i!=2 else 'stochastic',
                'state':'encerrado','result':'WIN','settlement_pending':False,'telegram':{'status':'enviado'}},str(i))
        meta=c.get('/api/history/days').json()
        assert meta['days']==[{'date':'2026-10-03','signals':2},{'date':'2026-10-02','signals':1}]
        today=c.get('/api/history?start_date=2026-10-03&end_date=2026-10-03').json()
        assert today['total']==2 and {r['id'] for r in today['items']}=={'1','2'}
        assert c.get('/api/history?strategy=stochastic').json()['total']==1
        assert len(store.all_signals())==3 and store.result_totals()['wins']==3


def test_day_export_keeps_full_evidence_and_leaves_database_unchanged(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Source(),TG())) as c:
        store=c.app.state.service.store;entry=int(datetime(2026,10,3,12,tzinfo=timezone.utc).timestamp())
        row={'id':'export','created_at':entry-120,'entry_at':entry,'expiry_at':entry+60,'asset':'EUR/USD',
             'state':'encerrado','result':'LOSS','settlement_pending':False,'telegram':{'status':'falha'},'evidence':{'original':'keep'}}
        store.add_signal(row,'export')
        response=c.get('/api/history/export-day?day=2026-10-03')
        assert response.status_code==200 and response.json()['signals'][0]['evidence']==row['evidence']
        assert 'attachment' in response.headers['content-disposition'] and store.signal('export')==row
