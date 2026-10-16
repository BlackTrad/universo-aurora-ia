"""History visibility must never reset risk, results, learning or pending settlement."""
from datetime import datetime,timezone
import sqlite3
import pytest
from fastapi.testclient import TestClient
from app.config import Config
from app.main import create_app
from app.store import Store
from tests.test_system import Feed,TG


def stamp(day):
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp()


def signal(store,sid,entry,state='encerrado',result='LOSS',pending=False):
    row={'id':sid,'entry_at':entry,'expiry_at':entry+60,'created_at':entry-120,'settled_at':entry+65,
         'asset':'EUR/USD','source_key':'deriv','source_identity':'test','strategy':'cci','direction':'CALL','score':90,
         'timeframe':'M1','duration_seconds':60,'state':state,'result':result,'settlement_pending':pending,
         'telegram':{'status':'enviado'},'evidence':{'rules_version':'1.22.10','original':'preserve'}}
    assert store.add_signal(row,sid)
    return row


def test_clear_day_uses_brasilia_entry_day_and_preserves_pending(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as c:
        store=c.app.state.service.store;midnight=stamp('2026-10-04T03:00:00')
        before=signal(store,'before',midnight-1)
        terminal=signal(store,'terminal',midnight)
        signal(store,'next',midnight+86400)
        signal(store,'active',midnight+60,'em andamento',None,True)
        signal(store,'settling',midnight+120,'encerrado',None,True)
        signal(store,'cancel',midnight+180,'cancelado',None,True)
        r=c.post('/api/history/clear',json={'scope':'day','period':'2026-10-04','confirmed':True})
        assert r.status_code==200 and r.json()['cleared']==2 and r.json()['pending_preserved']==2
        assert {s['id'] for s in c.get('/api/history').json()['items']}=={'before','next','active','settling'}
        assert store.signal('terminal')==terminal and store.signal('before')==before
        assert {s['id'] for s in store.active()}=={'active','settling'}
        exported=c.get('/api/history/export-day?day=2026-10-04').json()
        assert {s['id'] for s in exported['signals']}=={'terminal','active','settling','cancel'}


@pytest.mark.parametrize('month,start,end',[('2026-10','2026-10-01T03:00:00','2026-11-01T03:00:00'),('2026-12','2026-12-01T03:00:00','2027-01-01T03:00:00')])
def test_month_has_half_open_boundary_and_december_rollover(tmp_path,month,start,end):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as c:
        store=c.app.state.service.store;lo,hi=stamp(start),stamp(end)
        for sid,t in [('previous',lo-1),('first',lo),('last',hi-1),('following',hi)]:signal(store,sid,t)
        assert c.post('/api/history/clear',json={'scope':'month','period':month,'confirmed':True}).json()['cleared']==2
        assert {s['id'] for s in store.history()['items']}=={'previous','following'}


def test_clear_all_is_idempotent_and_does_not_reset_risk_or_learning(tmp_path):
    path=tmp_path/'db';store=Store(str(path));now=stamp('2026-10-04T12:00:00')
    # Both settlements must already be known at this cutoff.
    for i in range(2):signal(store,str(i),now-240+i*60)
    before=store.apex_limits(now);learning=store.learning_signals();originals=store.all_signals()
    assert before['pause_until']>now
    assert store.clear_history('all',None,now)['cleared']==2
    assert store.clear_history('all',None,now)['cleared']==0
    assert store.history()['total']==0 and store.history_days()==[] and store.history_facets()=={'assets':[],'strategies':[]}
    assert store.result_totals(include_archived=False)['losses']==0
    assert store.result_totals()['losses']==2 and store.all_signals()==originals
    assert store.learning_signals()==learning and store.apex_limits(now)==before
    assert store.current_signal() is None
    assert store.validation_report(now+100)['counts']['generated']==2
    assert store.validation_report(now+100)['history_visibility']['archived']==2
    assert store.db.execute('SELECT count(*) FROM history_clear_batches').fetchone()[0]==1
    with pytest.raises(sqlite3.IntegrityError):store.db.execute('DELETE FROM history_archives')
    store.db.close();store=Store(str(path))
    assert store.history()['total']==0 and store.apex_limits(now)==before
    assert store.result_totals()['losses']==2
    store.db.close()


@pytest.mark.parametrize('body',[
    {'scope':'all','confirmed':False}, {'scope':'all','confirmed':1},
    {'scope':'day','period':'2026-02-30','confirmed':True},
    {'scope':'day','period':'20261004','confirmed':True},
    {'scope':'month','period':'2026-13','confirmed':True},
    {'scope':'all','period':'2026-10','confirmed':True},
    {'scope':'all'},
])
def test_clear_requires_explicit_confirmation_and_valid_period(tmp_path,body):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as c:
        signal(c.app.state.service.store,'keep',stamp('2026-10-03T12:00:00'))
        assert c.post('/api/history/clear',json=body).status_code==422
        assert c.get('/api/history').json()['total']==1


def test_clear_is_local_and_manual_record_and_original_remain_accessible(tmp_path):
    with TestClient(create_app(Config(database=str(tmp_path/'db')),Feed(),TG())) as c:
        store=c.app.state.service.store;row=signal(store,'keep',stamp('2026-10-03T12:00:00'),result='WIN')
        body={'scope':'all','confirmed':True}
        assert c.post('/api/history/clear',json=body,headers={'Origin':'https://foreign.invalid'}).status_code==403
        assert c.post('/api/signals/keep/manual-confirmation',json={'executed':True,'result':'LOSS','broker':'Declared'}).status_code==200
        assert c.post('/api/history/clear',json=body).json()['cleared']==1
        detail=c.get('/api/signals/keep').json()
        assert detail['signal']==row and detail['manual_confirmations'][0]['result']=='LOSS'
        assert c.get('/api/state').json()['result_totals']['wins']==0
        assert c.get('/api/validation').json()['counts']['generated']==1
