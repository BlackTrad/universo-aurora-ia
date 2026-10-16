"""Synthetic software regressions: no external messages, trades or efficacy claims."""
from unittest.mock import patch
import sqlite3
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.market import Candle
from app.store import Store
from tests.test_system import Feed,TG


def signal(sid,now,**changes):
    return {'id':sid,'created_at':now,'emitted_at':now,'entry_at':now+120,'expiry_at':now+420,
            'state':'aguardando entrada','settlement_pending':True,'result':None,
            'telegram':{'status':'não configurado'},'asset':'EUR/USD','direction':'CALL','score':90,
            'strategy':'scan_all','source_key':'deriv',**changes}


@pytest.mark.parametrize('telegram',['não configurado','falha','expirado'])
def test_panel_publication_cancellation_does_not_release_hour_quota(tmp_path,telegram):
    store=Store(str(tmp_path/'risk.db'));now=1800000000
    store.save_settings({'hourly_limit':1})
    old=signal('first',now,telegram={'status':telegram})
    assert store.add_apex_signal(old,'movement1')[0]
    store.patch('first',{'state':'cancelado','result':'CANCELADO','settlement_pending':False})
    gate=store.apex_limits(now+5,now+125)
    assert gate['active']==0 and gate['used']==1 and gate['remaining']==0
    assert not store.add_apex_signal(signal('next',now+5),'movement2')[0]
    store.db.close();store=Store(str(tmp_path/'risk.db'))
    store.clear_history('all',None,now+10)
    assert store.apex_limits(now+15,now+135)['used']==1
    assert store.apex_limits(now+5,old['entry_at']+3600)['used']==0
    store.db.close()


def test_incomplete_and_future_outcomes_cannot_trigger_loss_pause():
    store=Store(':memory:');now=1800000000
    store.add_signal(signal('pending',now-600,result='LOSS',state='encerrado',settled_at=now-50),'pending')
    store.add_signal(signal('future',now-600,result='LOSS',state='encerrado',settlement_pending=False,settled_at=now+30),'future')
    store.add_signal(signal('live',now-600,result='LOSS',state='em andamento',settlement_pending=False,settled_at=now-40),'live')
    assert store.apex_limits(now)['pause_until']==0
    store.db.close()


def test_startup_failure_closes_store_and_releases_file_lock(tmp_path):
    config=Config(database=str(tmp_path/'startup.db'));dbs=[]
    original=Store.__init__
    def capture(self,path):original(self,path);dbs.append(self.db)
    with patch.object(Store,'__init__',capture):
        try:
            with patch.object(Store,'recover',side_effect=RuntimeError('controlled recovery failure')):
                with TestClient(create_app(config,Feed(),TG())):pass
        except RuntimeError as error:
            assert str(error)=='controlled recovery failure'
            # Retain traceback while attempting another lifespan: no GC-dependent release.
            with TestClient(create_app(config,Feed(),TG())) as client:
                assert client.get('/').status_code==200
                assert client.get('/api/state').json()['scan']['active'] is False
        else:raise AssertionError('Expected recovery failure')
    for db in dbs:
        with pytest.raises(sqlite3.ProgrammingError):db.execute('SELECT 1')


def test_recovery_finds_interrupted_analysis_beyond_recent_100():
    store=Store(':memory:')
    store.analysis({'id':'interrupted','created_at':1,'status':'analisando','checks':[]})
    for i in range(120):store.analysis({'id':str(i),'created_at':i+2,'status':'prévia','checks':[]})
    store.recover(1000)
    import json
    record=json.loads(store.db.execute("SELECT payload FROM analyses WHERE id='interrupted'").fetchone()[0])
    assert record['status']=='interrompida' and record['created_at']==1
    store.db.close()


def test_observation_report_only_contains_outcomes_known_at_cutoff():
    from app.selection_observer import SelectionObserver
    from tests.test_validation_1240 import context,capture
    from tests.test_system import fixture
    observer=SelectionObserver(':memory:');now=fixture()[-1].t+305
    session=context(observer,now);row=capture(observer,session,now)
    settled=row['expiry_at']+65
    observer.patch(row,state='settled',result='WIN',settled_at=settled,entry_price=100,expiry_price=101,
        settlement_source_identity=row['source_identity'],
        settlement_evidence=[Candle(row['entry_at'],100,100,100,100).json(),
                             Candle(row['expiry_at'],101,101,101,101).json()])
    report=observer.report(now+5)
    group=report['sessions'][0]['groups'][0]
    assert group['pending']==1 and group['wins']==0 and group['evaluated']==0
    assert observer.rows()[0]['result']=='WIN'
    assert observer.report(settled)['sessions'][0]['groups'][0]['wins']==1
    observer.db.close()


def test_observation_future_session_is_not_visible_in_past_report():
    from app.selection_observer import SelectionObserver
    from tests.test_validation_1240 import context,capture
    from tests.test_system import fixture
    observer=SelectionObserver(':memory:');now=fixture()[-1].t+305
    session=context(observer,now);capture(observer,session,now)
    assert observer.report(now-1)['sessions']==[]
    observer.db.close()
