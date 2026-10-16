"""Regressions for read-only Deriv catalogue separation in 1.24.1."""
import asyncio
from fastapi.testclient import TestClient
from app.config import Config
from app.deriv import DerivProvider
from app.main import create_app
from tests.test_chart import Source
from tests.test_system import TG


class BrowsableDeriv(Source):
    name='Deriv';identity='deriv:test';timeframes=['M1','M2','M3','M5']
    async def browse_instruments(self):
        return [
            {'symbol':'EUR/USD','availability':'open','detail':'test catalogue'},
            {'symbol':'GBP/USD','availability':'closed','detail':'test catalogue'},
        ]


def test_unmonitored_deriv_asset_is_visual_only_and_does_not_expand_scanner(tmp_path):
    cfg=Config(database=str(tmp_path/'db'),assets=['EUR/USD'])
    with TestClient(create_app(cfg,BrowsableDeriv(),TG())) as c:
        rows=c.get('/api/instruments?source=deriv&page_size=24').json()['items']
        assert [r['symbol'] for r in rows]==['EUR/USD','GBP/USD']
        gbp=next(r for r in rows if r['symbol']=='GBP/USD')
        assert gbp['availability']=='closed'
        assert not gbp['analysis_selectable'] and not gbp['analysis_enabled']
        chart=c.get('/api/chart?asset=GBP/USD&source=deriv&timeframe=M1')
        assert chart.status_code==200 and chart.json()['asset']=='GBP/USD'
        rejected=c.post('/api/analyze',json={'provider':'deriv','assets':['GBP/USD'],'timeframe':'M1','duration_seconds':60})
        assert rejected.status_code==422
        assert c.app.state.service.available_assets==['EUR/USD']


def test_deriv_live_catalogue_parses_only_regular_forex_symbols():
    provider=DerivProvider(Config(assets=['EUR/USD']))
    async def catalog():
        return {'active_symbols':[
            {'underlying_symbol':'frxEURUSD','market':'forex','exchange_is_open':1,'is_trading_suspended':0},
            {'underlying_symbol':'frxGBPUSD','market':'forex','exchange_is_open':0,'is_trading_suspended':0},
            {'underlying_symbol':'frxUSDJPY','market':'forex','exchange_is_open':1,'is_trading_suspended':1},
            {'underlying_symbol':'R_100','market':'synthetic_index','exchange_is_open':1,'is_trading_suspended':0},
            {'underlying_symbol':'cryBTCUSD','market':'cryptocurrency','exchange_is_open':1,'is_trading_suspended':0},
        ]}
    provider.catalog=catalog
    rows=asyncio.run(provider.browse_instruments())
    assert rows==[
        {'symbol':'EUR/USD','availability':'open','detail':'Deriv active_symbols; pares de moedas regulares como referência de preço.'},
        {'symbol':'GBP/USD','availability':'closed','detail':'Deriv active_symbols; pares de moedas regulares como referência de preço.'},
        {'symbol':'USD/JPY','availability':'suspended','detail':'Deriv active_symbols; pares de moedas regulares como referência de preço.'},
    ]
    # Browsing support does not mutate the operational allowlist.
    assert provider.config.assets==['EUR/USD']
