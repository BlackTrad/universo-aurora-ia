"""Behavioral checks for APEX gates, measured results and FVG mitigation."""
import unittest
from unittest.mock import patch
import asyncio
import sys
import types

from app.strategies import scan_all as evaluate
from app.market import Candle, CandleBatch
from app.store import Store
from app.strategies.ema_macd import evaluate as ema_macd


class ApexReviewTests(unittest.TestCase):
    def test_two_active_signal_cap(self):
        store = Store(':memory:')
        now = 1_800_010_000
        for i in range(2):
            entry = now + 60 + i * 10
            signal = {'id': f'active-{i}', 'created_at': now, 'entry_at': entry,
                      'expiry_at': entry + 60, 'state': 'aguardando entrada',
                      'result': 'Resultado indisponível'}
            self.assertTrue(store.add_apex_signal(signal, signal['id'])[0])
        third = {'id': 'active-2', 'created_at': now, 'entry_at': now + 80,
                 'expiry_at': now + 140, 'state': 'aguardando entrada',
                 'result': 'Resultado indisponível'}
        ok, message = store.add_apex_signal(third, third['id'])
        self.assertFalse(ok)
        self.assertIn('2 sinais simultâneos', message)


    def test_limits_and_metrics_include_real_outcomes_only(self):
        store = Store(':memory:')
        now = 1_800_000_000
        for i in range(5):
            signal = {'id': str(i), 'created_at': now + i, 'entry_at': now + 60 + 60*i,
                      'expiry_at': now + 120 + 60*i, 'state': 'encerrado',
                      'result': 'WIN' if i < 3 else 'LOSS', 'settled_at': now + i,
                      'strategy_resolved': 'order_block', 'asset': 'EUR/USD',
                      'timeframe': 'M1', 'duration_seconds': 60}
            ok, message = store.add_apex_signal(signal, str(i))
            self.assertTrue(ok, message)
        self.assertEqual(store.apex_limits(now + 5, now + 360)['remaining'], 5)
        self.assertGreater(store.apex_limits(now + 5)['pause_until'], now + 5)
        group = store.apex_metrics()[0]
        self.assertEqual((group['wins'], group['losses'], group['evaluated']), (3, 2, 5))
        self.assertIsNone(group['win_rate_pct'])
        self.assertFalse(store.add_apex_signal({'id': 'blocked', 'created_at': now + 6,
            'entry_at': now + 360}, 'blocked')[0])

    def test_macd_strategy_requires_history(self):
        rows = [Candle(i * 60 + 60, 100, 101, 99, 100) for i in range(39)]
        result, reason = ema_macd(rows)
        self.assertIsNone(result)
        self.assertIn('insuficiente', reason)

    def test_macd_crossing_uses_closed_candles(self):
        values = [110 - i * .01 for i in range(35)] + [109.65 + j * .01 for j in range(1, 14)]
        rows = []
        for i, price in enumerate(values):
            prior = values[i-1] if i else price
            rows.append(Candle((i+1)*60, prior, max(prior, price)+.005,
                               min(prior, price)-.005, price))
        before, _ = ema_macd(rows[:-1])
        after, _ = ema_macd(rows)
        self.assertIsNone(before)
        self.assertEqual(after['direction'], 'CALL')

    def test_settlement_requires_exact_expiry_and_same_source(self):
        with patch.dict(sys.modules, {'dotenv': types.SimpleNamespace(set_key=lambda *args, **kwargs: None)}):
            from app.service import Service
        now = [1_800_001_020]
        class Config:
            strategy = 'order_block'
            assets = ['EUR/USD']
            max_age = 330
        class Provider:
            name = 'Deriv'
            identity = 'deriv-regular'
            def __init__(self): self.rows = None
            async def candles(self, *args): return self.rows
        class Telegram:
            configured = False
        provider = Provider()
        store = Store(':memory:')
        service = Service(Config(), store, provider, Telegram(), clock=lambda: now[0])
        entry = now[0] - 300
        expiry = entry + 60
        row = {'id': 'exact', 'created_at': entry - 80, 'entry_at': entry,
               'expiry_at': expiry, 'duration_seconds': 60, 'state': 'encerrado',
               'result': 'Resultado indisponível', 'settlement_pending': True,
               'source_key': 'deriv', 'source_identity': 'deriv-regular',
               'asset': 'EUR/USD', 'direction': 'CALL', 'telegram': {'status': 'não configurado'}}
        store.add_signal(row, 'exact')
        # Expiration quote missing: never infer from the next available candle.
        provider.rows = CandleBatch([Candle(entry, 100, 101, 99, 100),
                                     Candle(expiry+60, 101, 102, 100, 101)], 'deriv-regular')
        asyncio.run(service.settle(row))
        self.assertEqual(store.signal('exact')['result'], 'Resultado indisponível')
        self.assertTrue(store.signal('exact')['settlement_pending'])
        provider.rows = CandleBatch([Candle(entry, 100, 101, 99, 100),
                                     Candle(expiry, 101, 102, 100, 101)], 'other-source')
        asyncio.run(service.settle(row))
        self.assertTrue(store.signal('exact')['settlement_pending'])
        provider.rows = CandleBatch([Candle(entry, 100, 101, 99, 100),
                                     Candle(expiry, 101, 102, 100, 101)], 'deriv-regular')
        asyncio.run(service.settle(row))
        settled = store.signal('exact')
        self.assertEqual(settled['result'], 'WIN')
        self.assertEqual(settled['settlement_expiry_at'], expiry)
        self.assertEqual(settled['settlement_source_identity'], 'deriv-regular')


    def test_rolling_limit_uses_entry_not_creation_time(self):
        store = Store(':memory:')
        now = 1_800_010_000
        for i in range(10):
            entry = now + 470 + i * 10
            signal = {'id': f'entry-{i}', 'created_at': now + i,
                      'entry_at': entry, 'expiry_at': entry + 60,
                      'state': 'encerrado', 'result': 'WIN', 'settled_at': entry + 60}
            assert store.add_apex_signal(signal, signal['id'])[0]
        proposed = now + 3901
        assert all(proposed - 3600 < now + 470 + i * 10 for i in range(10))
        assert store.apex_limits(proposed, proposed)['remaining'] == 0
        assert store.add_apex_signal({'id': 'sixth', 'created_at': proposed,
            'entry_at': proposed, 'state': 'aguardando entrada'}, 'sixth')[0] is False
        store.db.close()

    def test_metrics_separate_legacy_and_deriv(self):
        store = Store(':memory:')
        for source in ('deriv', 'twelvedata'):
            signal = {'id': source, 'created_at': 100, 'entry_at': 160,
                'state': 'encerrado', 'source_key': source,
                'strategy': 'order_block', 'asset': 'EUR/USD',
                'timeframe': 'M1', 'duration_seconds': 60,
                'result': 'WIN' if source == 'deriv' else 'LOSS'}
            assert store.add_signal(signal, source)
        groups = {row['source_key']: row for row in store.apex_metrics()}
        assert groups['deriv']['wins'] == 1 and groups['deriv']['losses'] == 0
        assert groups['twelvedata']['losses'] == 1 and groups['twelvedata']['wins'] == 0
        store.db.close()

if __name__ == '__main__':
    unittest.main()
