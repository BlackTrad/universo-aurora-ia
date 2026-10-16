"""Preview cannot publish a signal; money estimates use saved values at emission."""
import asyncio
import pytest
from fastapi.testclient import TestClient
from app.main import create_app
from app.config import Config
from app.store import Store
from app.market import Candle
from tests.test_system import setup, drain, Feed, TG


def test_preview_finds_candidates_without_emitting(tmp_path):
    async def run():
        service, _ = setup(tmp_path)
        service.start(['EUR/USD'], 300, 'M5', preview=True)
        await drain(service)
        analysis = service.store.analyses()[0]
        assert analysis['preview'] is True
        assert analysis['ranking']
        assert not service.store.signals()
        assert service.telegram.calls == 0
    asyncio.run(run())


@pytest.mark.parametrize('end_price,expected',[(101,8.0),(99,-10.0),(100,0.0)])
def test_settlement_estimate_is_frozen_per_signal(tmp_path,end_price,expected):
    async def run():
        service, clock = setup(tmp_path)
        service.store.save_settings({'assets':['EUR/USD'],'timeframe':'M5','duration_seconds':300,
                                     'stake_brl':10.0,'payout_pct':80.0})
        service.start(['EUR/USD'], 300, 'M5')
        await drain(service)
        signal = service.store.signals()[0]
        assert signal['stake_brl'] == 10.0
        assert signal['payout_pct'] == 80.0
        # Changing panel settings after emission must not rewrite the recorded amount.
        service.store.save_settings({'assets':['EUR/USD'],'stake_brl':20.0,'payout_pct':90.0})
        entry, expiry = signal['entry_at'], signal['expiry_at']
        source = signal['source_identity']
        service.provider.identity = source
        service.provider.settlement = [Candle(entry,100,100,100,100),
                                       Candle(expiry,end_price,end_price,end_price,end_price)]
        clock[0] = expiry + 65
        await service.settle(signal)
        result = service.store.signal(signal['id'])
        assert signal['direction']=='CALL'
        assert result['result'] == ('WIN' if end_price>100 else 'LOSS' if end_price<100 else 'EMPATE')
        assert result['estimated_pnl_brl'] == expected
        reopened=Store(service.config.database)
        try:assert reopened.signal(signal['id'])['estimated_pnl_brl']==expected
        finally:reopened.db.close()
    asyncio.run(run())


def test_money_settings_validation_and_legacy_preservation(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as client:
        base={'provider':'deriv','assets':['EUR/USD'],'timeframe':'M1','duration_seconds':60}
        for amounts in ({'stake_brl':10}, {'payout_pct':80}, {'stake_brl':-1,'payout_pct':80},
                        {'stake_brl':10,'payout_pct':101}, {'stake_brl':.001,'payout_pct':80}):
            assert client.put('/api/settings',json={**base,**amounts}).status_code==422
        assert client.put('/api/settings',json={**base,'stake_brl':10,'payout_pct':80}).status_code==200
        # Older clients omit the money fields; they must not erase saved values.
        assert client.put('/api/settings',json=base).status_code==200
        settings=client.get('/api/state').json()['settings']
        assert settings['stake_brl']==10 and settings['payout_pct']==80
        assert client.put('/api/settings',json={**base,'stake_brl':None,'payout_pct':None}).status_code==200
        assert client.get('/api/state').json()['settings']['stake_brl'] is None
