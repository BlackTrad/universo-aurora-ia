"""Preserve native user selections and recover compatible learning after upgrades."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.config import Config
from app.deriv import DerivProvider
from app.service import Service
from app.store import Store
from tests.test_evolution_1250 import signal
from tests.test_system import Feed, TG


SCOPES = [('forex', ['USD/JPY', 'EUR/USD']),
          ('crypto_binary', ['ETH/USD', 'BTC/USD'])]
PREVIOUS_RELEASES = ['3.6.0', '3.7.0', '3.8.0', '3.8.1']


def automatic_service(config, store):
    provider = DerivProvider(config)
    return Service(config, store, provider, SimpleNamespace(configured=False),
                   automatic_only=True, automatic_sources=True)


@pytest.mark.parametrize('scope,assets', SCOPES)
def test_initial_monitoring_uses_exact_configured_selection(scope, assets):
    config = Config(database=':memory:', asset_scope=scope, assets=assets)
    store = Store(':memory:')
    try:
        service = automatic_service(config, store)
        assert service.settings()['assets'] == assets
        assert service.settings()['source_mode'] == 'automatic'
        assert not service.scan['active'] and not service.price_streams.tasks
    finally:
        store.close()


@pytest.mark.parametrize('scope,assets', SCOPES)
@pytest.mark.parametrize('source_mode', [None, 'manual'])
def test_automatic_migration_preserves_saved_assets_period_and_limits(scope, assets, source_mode):
    store = Store(':memory:')
    selection = assets[::-1]
    store.save_settings({'provider': 'deriv', 'source_mode': source_mode, 'assets': selection,
                         'timeframe': 'M1', 'duration_seconds': 180, 'hourly_limit': 4,
                         'stake_brl': 5, 'payout_pct': 90})
    config = Config(database=':memory:', asset_scope=scope, assets=[assets[0]])
    try:
        service = automatic_service(config, store)
        settings = service.settings()
        assert settings['assets'] == selection and settings['timeframe'] == 'M1'
        assert settings['duration_seconds'] == 180 and settings['hourly_limit'] == 4
        assert settings['stake_brl'] == 5 and settings['payout_pct'] == 90
        again = automatic_service(config, store)
        assert again.settings() == settings
    finally:
        store.close()


@pytest.mark.parametrize('scope,assets', SCOPES)
def test_removed_saved_instruments_fall_back_to_config_without_adding_pairs(scope, assets):
    store = Store(':memory:')
    store.save_settings({'assets': ['UNSUPPORTED'], 'timeframe': 'M15', 'source_mode': 'manual'})
    config = Config(database=':memory:', asset_scope=scope, assets=assets)
    try:
        service = automatic_service(config, store)
        assert service.settings()['assets'] == assets
        assert service.settings()['timeframe'] == 'M5'
        store.save_settings({**service.settings(), 'assets': ['UNSUPPORTED']})
        assert service.settings()['assets'] == assets
    finally:
        store.close()


@pytest.mark.parametrize('scope,assets', SCOPES)
def test_existing_automatic_panel_selection_has_priority_over_env_defaults(scope, assets):
    store = Store(':memory:')
    store.save_settings({'assets': [assets[1]], 'timeframe': 'M1', 'source_mode': 'automatic'})
    try:
        service = automatic_service(Config(database=':memory:', asset_scope=scope, assets=assets), store)
        assert service.settings()['assets'] == [assets[1]]
        assert service.settings()['timeframe'] == 'M1'
    finally:
        store.close()


@pytest.mark.parametrize('previous_version', PREVIOUS_RELEASES)
def test_previous_release_learning_survives_result_delivery_and_retry(previous_version):
    store = Store(':memory:')
    original = signal(settled_by_version=previous_version)
    try:
        store.add_signal(original, 'original')
        store.defer_learning_feedback(original['id'], 210000)
        store.patch(original['id'], {'telegram_result': {'status': 'enviado'}}, 'telegram result', 200000)
        assert store.pending_learning_feedback(209999) == []
        pending = store.pending_learning_feedback(210000)
        assert [row['id'] for row in pending] == [original['id']]
        origin = json.loads(store.db.execute('SELECT payload FROM signal_origins').fetchone()[0])
        assert origin == original
    finally:
        store.close()


def test_missing_old_learning_job_is_recovered_without_rewriting_signal(tmp_path):
    path = tmp_path/'upgrade.db'
    store = Store(path)
    original = signal(settled_by_version='3.8.0')
    store.add_signal(original, 'original')
    store.db.execute('DELETE FROM feedback_jobs')
    store.db.execute("UPDATE feedback_queue_meta SET version='previous-release-policy'")
    store.db.commit()
    before = tuple(store.db.execute('SELECT * FROM signals').fetchone())
    store.close()
    reopened = Store(path)
    try:
        assert [row['id'] for row in reopened.pending_learning_feedback(200000)] == ['0']
        assert tuple(reopened.db.execute('SELECT * FROM signals').fetchone()) == before
        assert json.loads(reopened.db.execute('SELECT payload FROM signal_origins').fetchone()[0]) == original
    finally:
        reopened.close()


def test_unknown_release_is_not_assumed_to_share_settlement_rules():
    store = Store(':memory:')
    try:
        store.add_signal(signal(settled_by_version='9.9.9'), 'unknown')
        assert store.pending_learning_feedback(200000) == []
    finally:
        store.close()


def test_recovered_compatible_result_reaches_learning_once_without_starting_scanner():
    async def scenario():
        store = Store(':memory:')
        service = Service(Config(database=':memory:'), store, Feed(), TG(), clock=lambda: 200000)
        try:
            store.add_signal(signal(settled_by_version='3.7.0'), 'original')
            for _ in range(2):
                await service.tick()
                while service.tasks:
                    await asyncio.gather(*list(service.tasks))
                    await asyncio.sleep(0)
            feedback = store.latest_learning_feedback(200000)
            assert feedback is not None and feedback['signal_id'] == '0'
            assert feedback['valid_quote_outcome'] is True
            assert store.db.execute('SELECT count(*) FROM learning_feedback').fetchone()[0] == 1
            assert not service.scan['active']
        finally:
            await service.price_streams.close()
            store.close()
    asyncio.run(scenario())
