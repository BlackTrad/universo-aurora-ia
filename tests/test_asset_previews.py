"""Preview regressions. Synthetic fixtures verify software, not accuracy."""
import asyncio
from copy import deepcopy
from unittest.mock import patch
import pytest
from app.analysis_preview import asset_previews
from app.market import Candle
from app.store import Store
from app.strategies import STRATEGIES, scan_all
from tests.test_system import setup, drain, BASE


def test_every_requested_asset_remains_visible_without_candidates(tmp_path):
    async def run():
        service, _ = setup(tmp_path)
        service.provider.rows=[Candle(BASE+i*300,100,101,99,100) for i in range(50)]
        service.start(['EUR/USD','GBP/USD'],300,'M5',preview=True)
        await drain(service)
        analysis=service.snapshot()['analyses'][0]
        assert not analysis['ranking'] and not service.store.signals()
        rows=analysis['asset_previews']
        assert [r['asset'] for r in rows]==['EUR/USD','GBP/USD']
        assert all(r['status']=='no_setup' and r['reason'] for r in rows)
        assert all(r['data_at']==service.provider.rows[-1].t+300 for r in rows)
        assert service.telegram.calls==0 and service.provider.calls==2
    asyncio.run(run())


@pytest.mark.parametrize('message,code,status',[
    ('Fonte indisponível','network','data_error'),
    ('Limite da fonte','rate_limit','data_error'),
    ('Mercado fechado','market_closed','market_closed'),
])
def test_invalid_data_is_visible_without_a_fake_opportunity(tmp_path,message,code,status):
    async def run():
        from app.market import DataError
        service, _=setup(tmp_path)
        async def fail(*args):raise DataError(message,code)
        service.provider.candles=fail
        service.start(['EUR/USD'],300,'M5',preview=True)
        await drain(service)
        row=service.snapshot()['analyses'][0]['asset_previews'][0]
        assert row['status']==status and row['reason']==message and row['code']==code
        assert row['candidate'] is None and row['data_at'] is None
        assert not service.store.signals() and service.telegram.calls==0
    asyncio.run(run())




def test_qualified_preview_and_emission_are_distinct(tmp_path):
    async def run():
        service, _=setup(tmp_path)
        service.start(['EUR/USD'],300,'M5',preview=True)
        await drain(service)
        row=service.snapshot()['analyses'][0]['asset_previews'][0]
        assert row['status']=='candidate' and row['candidate']['direction']=='CALL'
        assert row['signal_id'] is None and not service.store.signals()
        assert service.telegram.calls==0
        service.start(['EUR/USD'],300,'M5')
        await drain(service)
        row=service.snapshot()['analyses'][0]['asset_previews'][0]
        assert row['status']=='emitted' and row['signal_id']==service.store.signals()[0]['id']
        assert service.telegram.calls==1
        service.start(['EUR/USD'],300,'M5')
        await drain(service)
        row=service.snapshot()['analyses'][0]['asset_previews'][0]
        assert row['status']=='blocked' and 'duplicado' in row['reason']
        assert row['candidate'] is None and service.telegram.calls==1
    asyncio.run(run())


def test_progress_stop_and_restart_keep_asset_diagnostics(tmp_path):
    async def run():
        service, _=setup(tmp_path)
        started=asyncio.Event()
        async def wait(*args):
            started.set();await asyncio.Event().wait()
        service.provider.candles=wait
        first=service.start(['EUR/USD','GBP/USD'],300,'M5',preview=True)
        assert all(r['status']=='waiting' for r in first['asset_previews'])
        await started.wait()
        rows=service.snapshot()['analyses'][0]['asset_previews']
        assert [r['status'] for r in rows]==['analyzing','waiting']
        await service.stop_analysis()
        rows=service.snapshot()['analyses'][0]['asset_previews']
        assert all(r['status']=='interrupted' and r['candidate'] is None for r in rows)
        reopened=Store(service.config.database)
        try:assert reopened.analyses()[0]['asset_previews']==rows
        finally:reopened.db.close()
        assert not service.store.signals() and service.telegram.calls==0
    asyncio.run(run())


@pytest.mark.parametrize('kind,code,status',[
    ('no_setup',None,'no_setup'),('engine_error','engine_error','engine_error'),
    ('discarded','entry_late','discarded'),('contract_blocked','contract_metadata','blocked'),
    ('pause',None,'blocked'),('limit',None,'blocked'),('duplicate',None,'blocked'),
])
def test_legacy_projection_preserves_original_checks(kind,code,status):
    original={'id':'legacy','assets':['EUR/USD','GBP/USD'],'source_key':'deriv','timeframe':'M5',
              'status':'sem oportunidade','created_at':1,'finished_at':2,'ranking':[],
              'checks':[{'asset':'EUR/USD','kind':kind,'code':code,'reason':'Motivo original'}]}
    before=deepcopy(original)
    rows=asset_previews(original)
    assert rows[0]['status']==status and rows[0]['reason']=='Motivo original'
    assert rows[0]['candidate'] is None and rows[0]['duration_seconds'] is None
    assert original==before  # The projection must not rewrite history.


def test_display_candidates_do_not_override_later_discard_checks():
    analysis={'id':'late','assets':['EUR/USD'],'source_key':'deriv','timeframe':'M5','duration_seconds':60,
              'status':'descartada por atraso','created_at':1,'finished_at':2,
              'ranking':[{'asset':'EUR/USD','direction':'CALL','score':99}],
              'checks':[{'asset':'EUR/USD','kind':'discarded','reason':'Prazo insuficiente'}]}
    row=asset_previews(analysis)[0]
    assert row['status']=='discarded' and row['candidate'] is None
    assert row['reason']=='Prazo insuficiente'


def test_open_candle_never_enters_preview_decision(tmp_path):
    async def run():
        service, clock=setup(tmp_path)
        last=service.provider.rows[-1]
        service.provider.rows.append(Candle(last.t+300,100,150,50,130))
        service.start(['EUR/USD'],300,'M5',preview=True)
        await drain(service)
        row=service.snapshot()['analyses'][0]['asset_previews'][0]
        assert row['data_at']==last.t+300<=clock[0]
        decision=service.store.decisions()['items'][0]['decision']
        assert decision['candles'][-1]['t']==last.t
        assert all(c['t']+300<=decision['created_at'] for c in decision['candles'])
    asyncio.run(run())
