"""Production consensus/contract regression checks; no external calls or orders."""
import asyncio
from types import SimpleNamespace

import pytest

from app.config import Config
from app.market import CandleBatch
from app.service import Service
from app.store import Store
from backend.ai.pipeline import AIPipeline
from tests.book_fixtures import scenario
from tests.test_book_12612 import LiveFeed
from tests.test_system import TG, drain, fixture


class CryptoFeed(LiveFeed):
    name='Deriv'
    identity='deriv:crypto:binary:public'
    assets=['BTC/USD']


@pytest.mark.parametrize('scope',('forex','crypto_binary'))
def test_production_never_arms_live_only_entries_without_full_gates(scope):
    rows=scenario('pa_flow_hammer')['rows']
    clock=[rows[-1].t+305]
    feed=CryptoFeed(clock)
    store=Store(':memory:')
    service=Service(Config(database=':memory:',asset_scope=scope),store,feed,TG(),
                    clock=lambda:clock[0],automatic_only=True,automatic_sources=True,
                    providers={'deriv':feed})
    service.scan={'active':True,'id':'controlled'}
    service.arm_intrabar({'scan_id':'controlled'},'BTC/USD',rows,rows,feed.identity,clock[0],{})
    assert not service.intrabar_contexts
    assert service.intrabar['code']=='intrabar_requires_verified_consensus'
    assert not store.signals()
    store.close()


@pytest.mark.parametrize('condition',('missing_consensus','low_payout','stopped'))
def test_production_cannot_commit_preexisting_legacy_live_context(condition):
    async def run():
        rows=scenario('pa_flow_hammer')['rows'];clock=[rows[-1].t+305]
        store=Store(':memory:');feed=CryptoFeed(clock)
        service=Service(Config(assets=['BTC/USD'],database=':memory:',asset_scope='crypto_binary'),
                        store,feed,TG(),clock=lambda:clock[0],automatic_only=True,
                        providers={'deriv':feed})
        if condition=='low_payout':
            store.save_settings({**store.settings({}),'payout_pct':85,'stake_brl':5})
        service.scan={'active':True,'id':'controlled','assets':['BTC/USD']}
        service.start(['BTC/USD'],300,'M5',scan_id='controlled')
        await service.current_analysis_task
        assert service.intrabar_contexts  # An actual native book setup, not a fabricated candidate.
        service.automatic_sources=True
        calls=[]
        async def forbidden(*args,**kwargs):
            calls.append(condition)
            raise AssertionError('Live-only entries must fail closed before external calls.')
        service.analyze_with_ai=forbidden
        service.ensure_binary_contract=forbidden
        if condition=='stopped':service.scan['active']=False
        assert await service.poll_intrabar()==[]
        assert not store.signals()
        assert not service.intrabar_contexts
        assert not calls and feed.price_calls==0
        assert service.intrabar['state']=='bloqueado'
        await drain(service)
        store.close()
    asyncio.run(run())


def test_final_proposal_timeout_discards_only_its_asset_and_keeps_other_candidate():
    class Broker:
        name='Deriv';identity='deriv:crypto:binary:public'
        assets=['BTC/USD','ETH/USD'];timeframes=['M1','M5']
        def __init__(self):self.checks={}
        async def candles(self,*args,**kwargs):return CandleBatch(fixture(),self.identity)
        async def supports(self,*args):pass
        async def confirmed_payout(self,asset,*args):
            self.checks[asset]=self.checks.get(asset,0)+1
            if asset=='BTC/USD' and self.checks[asset]==2:raise asyncio.TimeoutError()
            return .90
    async def run():
        store=Store(':memory:');broker=Broker();now=fixture()[-1].t+305
        service=Service(Config(database=':memory:',asset_scope='crypto_binary',
                        assets=broker.assets,strategy='candlestick_sr'),store,broker,TG(),
                        clock=lambda:now,automatic_sources=True,providers={'deriv':broker})
        async def fake_payload(*args,**kwargs):return SimpleNamespace(data={})
        async def fake_consensus(*args,**kwargs):
            return {'approved':True,'decision':{'direcao':'CALL','confianca':90},'votes':[]}
        service.build_ai_payload=fake_payload;service.analyze_with_ai=fake_consensus
        service.start(broker.assets,300,'M5')
        await service.current_analysis_task
        signals=store.signals();analysis=store.analyses()[0]
        assert [signal['asset'] for signal in signals]==['ETH/USD']
        assert broker.checks=={'BTC/USD':2,'ETH/USD':2}
        assert analysis['signals_emitted']==1 and analysis['status']=='sinal'
        assert any(check.get('asset')=='BTC/USD' and check.get('code')=='contract_timeout'
                   for check in analysis['checks'])
        await asyncio.sleep(0)
        await drain(service);store.close()
    asyncio.run(run())


def analysis_payload(asset='ETH/USD'):
    return {
        'ativo':asset,'mercado':'OPCOES_BINARIAS','fonte':'Deriv',
        'source_identity':'deriv:crypto:binary:public','timeframe':'M5',
        'expiracao_minutos':5,'timestamp_utc':'2026-10-15T12:00:05Z',
        'entrada_utc':'2026-10-15T12:02:00Z','preco_atual':None,'payout':.90,
        'candles_m1':[],'candles_m5':[{'t':1792065300}],
        'indicadores':{},'estrutura_smc':{'tendencia':'indisponivel'},
        'estrategias_backend':['pa_hammer'],'filtros_backend':[],
        'pre_sinal':{'direcao':'CALL'},
    }


@pytest.mark.parametrize('outcome',('complete','cancel','error'))
def test_ai_round_has_its_own_context_and_clears_previous_votes(outcome):
    async def run():
        started=asyncio.Event();finish=asyncio.Event()
        class Agent:
            name='Groq';provider_key='groq';model='test'
            async def analisar(self,payload):
                started.set();await finish.wait()
                return {'direcao':'CALL','confianca':80,'justificativa':'test',
                        'evidencias':[],'alertas':[],'dados_insuficientes':False}
        store=Store(':memory:')
        pipeline=AIPipeline([Agent()])
        if outcome=='error':
            async def broken(payload):
                started.set();await finish.wait();raise RuntimeError('controlled test failure')
            pipeline.process=broken
        service=Service(Config(database=':memory:'),store,SimpleNamespace(name='test'),TG(),
                        clock=lambda:1234,ai_pipeline=pipeline)
        service.ai_status.update(last_votes=[{'agente':'old','direcao':'PUT'}],
                                 last_decision={'direcao':'PUT'})
        task=asyncio.create_task(service.analyze_with_ai(analysis_payload()))
        await started.wait()
        assert service.ai_status['status']=='processing'
        assert service.ai_status['last_votes']==[] and service.ai_status['last_decision'] is None
        assert service.ai_status['context']=={
            'ativo':'ETH/USD','timeframe':'M5','fonte':'Deriv',
            'source_identity':'deriv:crypto:binary:public',
            'timestamp_utc':'2026-10-15T12:00:05Z','entrada_utc':'2026-10-15T12:02:00Z',
            'candle_at':1792065300}
        assert service.ai_status['analysis_context']['data_at']==1792065600
        if outcome=='cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        elif outcome=='error':
            finish.set()
            with pytest.raises(RuntimeError):await task
        else:
            finish.set();await task
            assert service.ai_status['last_votes'][0]['agente']=='Groq'
        assert service.ai_status['status']!='processing'
        if outcome!='complete':
            assert service.ai_status['last_votes']==[] and service.ai_status['last_decision'] is None
        assert service.ai_status['context']['ativo']=='ETH/USD'
        store.close()
    asyncio.run(run())
