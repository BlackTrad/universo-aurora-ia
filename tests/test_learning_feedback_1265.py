"""Causal feedback and existing preference policy; no efficacy claims."""
import asyncio
from copy import deepcopy
import sqlite3
import pytest
from app.learning_feedback import feedback
from app.store import Store
from app.market import Candle
from app.version import VERSION
from tests.test_system import setup,drain
from tests.test_evolution_1250 import signal


@pytest.mark.parametrize('price,outcome',[(101,'WIN'),(99,'LOSS'),(100,'EMPATE')])
def test_each_settlement_updates_feedback_without_rewriting_decision(tmp_path,price,outcome):
    async def run():
        service,clock=setup(tmp_path)
        service.start(['EUR/USD'],300,'M5');await drain(service)
        s=service.store.signals()[0];original=deepcopy(s['engine_explanation'])
        service.provider.settlement=[Candle(s['entry_at'],100,100,100,100),Candle(s['expiry_at'],price,price,price,price)]
        clock[0]=s['expiry_at']+65
        await service.settle(s);await drain(service)
        saved=service.store.signal(s['id']);last=service.snapshot()['learning']['last_feedback']
        assert saved['result']==outcome and saved['engine_explanation']==original
        assert last['result']==outcome and last['original_reasons']==s['reasons']
        assert last['known_at']>=saved['settled_at']>=saved['expiry_at']+60
        assert not last['rule_change_applied'] and not last['preference_enabled'] and not last['veto_enabled']
        assert last['context_sample']==(0 if outcome=='EMPATE' else 1)
        assert last['valid_quote_outcome'] and last['binary_training_result']==(outcome!='EMPATE')
        assert last['rules_version']==s['evidence']['rules_version']
        assert last['pipeline_version']==s['evidence']['pipeline_version']
        if outcome=='LOSS':assert 'causa técnica não comprovada' in last['interpretation']
        await service.learn_result(saved)
        assert service.store.db.execute('SELECT COUNT(*) FROM learning_feedback').fetchone()[0]==1
        reopened=Store(service.config.database)
        try:assert reopened.latest_learning_feedback(clock[0])==last
        finally:reopened.db.close()
    asyncio.run(run())


def test_unavailable_quote_is_recorded_but_not_used_as_a_loss(tmp_path):
    async def run():
        service,clock=setup(tmp_path)
        service.start(['EUR/USD'],300);await drain(service)
        s=service.store.signals()[0];clock[0]=s['expiry_at']+601
        service.provider.settlement=[]
        await service.settle(s);await drain(service)
        last=service.store.latest_learning_feedback(clock[0])
        assert last['result']=='Resultado indisponível' and not last['valid_quote_outcome']
        assert not last['context_sample'] and not last['veto_enabled']
    asyncio.run(run())


@pytest.mark.parametrize('mode',['pending','future_settled','future_expiry'])
def test_unknown_or_future_result_cannot_enter_feedback(tmp_path,mode):
    async def run():
        service,clock=setup(tmp_path)
        s=signal();clock[0]=100000
        if mode=='pending':s['settlement_pending']=True
        if mode=='future_settled':s['settled_at']=100001
        if mode=='future_expiry':s['expiry_at']=100020
        service.store.add_signal(s,'future')
        await service.learn_result(s)
        assert service.store.latest_learning_feedback(clock[0]) is None
    asyncio.run(run())


def test_out_of_order_results_update_the_latest_known_context_without_double_counting(tmp_path):
    async def run():
        service,clock=setup(tmp_path);clock[0]=100000;service.learning={}
        later=signal(1,'LOSS');earlier=signal(0,'WIN')
        for s in [later,earlier]:
            service.store.add_signal(s,s['id']);await service.learn_result(s)
        assert service.learning['max_context_sample']==2
        assert not service.learning['eligible_profiles'] and not service.learning['blocked_profiles']
        await asyncio.gather(service.learn_result(earlier),service.learn_result(earlier))
        assert service.store.db.execute('SELECT COUNT(*) FROM learning_feedback').fetchone()[0]==2
    asyncio.run(run())


@pytest.mark.parametrize('outcome,preference,veto',[('WIN',True,False),('LOSS',False,True)])
def test_existing_policy_requires_sixty_train_and_thirty_validation(tmp_path,outcome,preference,veto):
    async def run():
        service,clock=setup(tmp_path);clock[0]=200000;service.learning={}
        for i in range(89):service.store.add_signal(signal(i,outcome),str(i))
        await service.learn_result(service.store.signal('88'))
        assert service.learning['max_context_sample']==89
        assert not service.learning['eligible_profiles'] and not service.learning['blocked_profiles']
        last=signal(89,outcome);service.store.add_signal(last,'89');await service.learn_result(last)
        f=service.store.latest_learning_feedback(clock[0])
        assert f['context_sample']==90 and f['train_sample']==60 and f['validation_sample']==30
        assert f['preference_enabled']==preference and f['veto_enabled']==veto
        assert f['used_in_training_or_validation'] and not f['rule_change_applied']
    asyncio.run(run())


def test_feedback_is_append_only_and_hidden_before_known_time():
    store=Store(':memory:')
    value={'signal_id':'one','known_at':200,'result':'LOSS'}
    assert store.record_learning_feedback(value)
    assert not store.record_learning_feedback({**value,'result':'WIN'})
    assert store.latest_learning_feedback(199) is None
    assert store.latest_learning_feedback(200)==value
    with pytest.raises(sqlite3.IntegrityError):store.db.execute("UPDATE learning_feedback SET payload='{}'")
    with pytest.raises(sqlite3.IntegrityError):store.db.execute('DELETE FROM learning_feedback')


def test_restart_recovers_a_completed_outcome_with_interrupted_feedback(tmp_path):
    async def run():
        service,clock=setup(tmp_path);s=signal(settled_by_version=VERSION)
        clock[0]=100000;service.learning={}
        service.store.add_signal(s,s['id'])
        assert service.store.latest_learning_feedback(clock[0]) is None
        await service.tick();await drain(service)
        assert service.store.latest_learning_feedback(clock[0])['signal_id']==s['id']
        await service.tick();await drain(service)
        assert service.store.db.execute('SELECT COUNT(*) FROM learning_feedback').fetchone()[0]==1
        assert not service.scan['active']
    asyncio.run(run())


def test_learning_failure_isolated_from_result_and_retry_is_bounded(tmp_path):
    async def run():
        service,clock=setup(tmp_path);s=signal(settled_by_version=VERSION)
        clock[0]=100000;service.store.add_signal(s,s['id']);calls=[]
        def fail(now):calls.append(now);raise RuntimeError('Synthetic learning failure')
        service.historical_learning=fail
        await service.tick();await drain(service)
        assert service.store.signal(s['id'])['result']=='WIN' and len(calls)==1
        await service.tick();await drain(service);assert len(calls)==1
        clock[0]+=61;await service.tick();await drain(service);assert len(calls)==2
    asyncio.run(run())
