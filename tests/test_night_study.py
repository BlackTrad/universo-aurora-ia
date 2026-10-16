import json
from datetime import datetime
from app.logging_setup import BRASILIA
from app.market import Candle
from app.night_study import Study,window


def test_night_crosses_midnight_in_brasilia():
    now=datetime(2026,9,26,0,30,tzinfo=BRASILIA).timestamp()
    start,end=window(now)
    assert datetime.fromtimestamp(start,BRASILIA).strftime('%d %H')=='25 22'
    assert datetime.fromtimestamp(end,BRASILIA).strftime('%d %H')=='26 06'


def test_paper_limit_persistence_and_exact_expiration(tmp_path):
    path=tmp_path/'study.db';s=Study(path)
    candles=[Candle(300,100,101,99,100)]
    c={'direction':'CALL','score':85,'setup_candle':300}
    assert s.add('night','EUR/USD','ob',c,candles,302)
    assert not s.add('night','EUR/USD','ob',c,candles,302)
    for i in range(110):s.add('night','EUR/USD',str(i),c,candles,302)
    assert s.count('night')==100
    s.db.close();s=Study(path)
    row=s.pending()[0]
    assert row['entry_at']==420 and row['expiry_at']==720
    s.settle(row,[Candle(420,100,101,99,100),Candle(780,102,103,101,102)],785)
    assert len(s.pending())==100
    s.settle(row,[Candle(420,100,101,99,100),Candle(720,102,103,101,102)],785)
    assert len(s.pending())==99
    report=s.export('night',tmp_path/'export')
    assert report['observations']==100
    assert json.loads((tmp_path/'export/night-resumo.json').read_text())['target']==100
    s.db.close()


def test_missing_quote_never_becomes_a_win(tmp_path):
    s=Study(tmp_path/'study.db')
    s.add('night','EUR/USD','cci',{'direction':'CALL','score':85,'setup_candle':300},[],302)
    row=s.pending()[0]
    s.settle(row,[],row['expiry_at']+600)
    assert not s.pending()
    report=s.export('night',tmp_path/'export')
    cci=next(x for x in report['strategies'] if x['strategy']=='cci')
    assert cci['unavailable']==1 and cci['win_rate_pct'] is None
    s.db.close()
