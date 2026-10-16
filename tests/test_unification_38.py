"""Integration regressions for the three input archives, without live APIs."""
import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from app.config import Config,ConfigurationError
from app.source_routing import FOREX_ASSETS,CRYPTO_BINARY_ASSETS
from app.store import Store
from backend.ai.orchestrator import PlanetaryAgentOrchestrator
from tests.test_evolution_1250 import signal


@pytest.mark.parametrize('assets,expected',[(FOREX_ASSETS,'forex'),(CRYPTO_BINARY_ASSETS,'crypto_binary')])
def test_existing_native_pairs_are_preserved_in_order(tmp_path,assets,expected):
    (tmp_path/'.env').write_text('DATA_PROVIDER=deriv\nASSETS='+','.join(assets)+'\n')
    with patch.dict(os.environ,{},clear=True),patch('app.config.ROOT',tmp_path):
        cfg=Config.load()
    assert cfg.assets==list(assets) and cfg.asset_scope==expected


def test_new_install_defaults_keep_original_crypto_allowlist(tmp_path):
    with patch.dict(os.environ,{},clear=True),patch('app.config.ROOT',tmp_path):
        cfg=Config.load()
    assert cfg.asset_scope=='crypto_binary' and cfg.assets==list(CRYPTO_BINARY_ASSETS)


def test_public_environment_cannot_disable_binary_guards_with_legacy_scope(tmp_path):
    with patch.dict(os.environ,{'ASSET_SCOPE':'legacy'},clear=True),patch('app.config.ROOT',tmp_path):
        with pytest.raises(ConfigurationError,match='ASSET_SCOPE'):Config.load()


def pending(index=0,**changes):
    return signal(index,state='aguardando entrada',result='Resultado indisponível',settlement_pending=True,
                  created_at=59900,entry_at=60000,expiry_at=60300,**changes)


@pytest.mark.parametrize('direction',['CALL','PUT'])
def test_asset_active_signal_blocks_distinct_setup_and_opposed_vote(direction):
    store=Store(':memory:')
    try:
        assert store.add_apex_signal(pending(0),'first')[0]
        accepted,reason=store.add_apex_signal(pending(1,direction=direction),'different-fingerprint')
        assert not accepted and 'ativo' in reason and len(store.signals())==1
    finally:store.close()


def test_signal_for_another_native_asset_is_not_a_duplicate():
    store=Store(':memory:')
    try:
        assert store.add_apex_signal(pending(0),'first')[0]
        assert store.add_apex_signal(pending(1,asset='GBP/USD'),'second')[0]
    finally:store.close()


def test_duplicate_asset_commit_is_atomic_across_requests():
    store=Store(':memory:')
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda i:store.add_apex_signal(pending(i),'fp-'+str(i)),range(2)))
        assert sum(result[0] for result in results)==1 and len(store.signals())==1
    finally:store.close()


def test_cancellation_releases_asset_without_deleting_its_original():
    store=Store(':memory:')
    try:
        assert store.add_apex_signal(pending(0),'first')[0]
        store.patch('0',{'state':'cancelado','result':'CANCELADO','settlement_pending':False},'cancelado',59901)
        assert store.add_apex_signal(pending(1),'second')[0]
        assert store.signal('0')['state']=='cancelado' and len(store.signals())==2
    finally:store.close()


def test_healthy_queued_agents_receive_a_full_call_timeout():
    class Agent:
        provider_key='nvidia';model='fake'
        def __init__(self,index):self.name=str(index)
        async def analisar(self,data):
            await asyncio.sleep(.06)
            return {'direcao':'CALL','confianca':80,'justificativa':'test','evidencias':[],
                    'alertas':[],'dados_insuficientes':False}
    async def run():
        orchestrator=PlanetaryAgentOrchestrator([Agent(i) for i in range(4)],agent_timeout=.1,provider_concurrency=2)
        votes=await orchestrator.run_agents({'ativo':'BTC/USD'})
        assert all(vote['status']=='disponivel' for vote in votes)
        assert any(vote['queue_ms']>=40 for vote in votes)
    asyncio.run(run())
