"""Controlled UI fixtures only; never imported by the production app."""
import math
import time
import uuid

from fastapi import HTTPException
from app.market import Candle
from app.learning import RULES_VERSION, PIPELINE_VERSION
from tests.browser_release_server import app, feed


@app.post('/_test/seed_fibonacci')
async def seed_fibonacci():
    service=app.state.service
    now=time.time();entry=math.ceil((now+180)/60)*60
    rows=await feed.candles('GBP/USD',300,100)
    bottom=min(rows[:-2],key=lambda c:c.l);top=max(rows[:-2],key=lambda c:c.h)
    level=(bottom.l+top.h)/2;sid=uuid.uuid4().hex
    s={'id':sid,'asset':'GBP/USD','source_key':'deriv','source':feed.name,'source_identity':feed.identity,
       'direction':'CALL','score':85,'created_at':now,'emitted_at':now,'entry_at':entry,'expiry_at':entry+300,
       'timeframe':'M5','duration_seconds':300,'strategy_resolved':'fibonacci','strategy':'scan_all',
       'reasons':['Dados sintéticos para conferir a interface. Não executar.'],
       'state':'aguardando entrada','result':'Resultado indisponível','settlement_pending':True,
       'result_kind':'indicativo','broker_confirmed':False,'telegram':{'status':'enviado'},
       'entry_validation':{'status':'pendente','scheduled_at':entry-45,'atr':1},
       'observed_win_rate':{'kind':'historical_observed','basis':'indicative_external','sample':0,'rate_pct':None},
       'setup_contract':{'version':'1.26.2-setup-1','engine':'fibonacci','direction':'CALL',
                         'retracement_ratio':.5,'anchors':[bottom.t,top.t],
                         'confirmed_at':rows[-1].t+300,'levels':{'frozen_fibonacci_level':level}},
       'evidence':{'candles':[c.json() for c in rows],'rules_version':RULES_VERSION,
                   'pipeline_version':PIPELINE_VERSION,'regime':'tendencial','configuration':{'id':'ui-test-fixed'}}}
    service.store.add_signal(s,sid)
    # State transitions are prescribed only in this UI fixture. Backend
    # lifecycle and entry validation remain covered by the normal tests.
    async def idle():pass
    service.tick=idle
    app.state.demo_signal=s
    return {'id':sid}


@app.post('/_test/signal_stage')
async def signal_stage(stage:str):
    service=app.state.service;s=app.state.demo_signal
    patch={'state':'aguardando entrada','settlement_pending':True,'result':'Resultado indisponível',
           'telegram':{'status':'enviado'},'entry_validation':{'status':'pendente','scheduled_at':s['entry_at']-45,'atr':1}}
    clock=s['created_at']+1
    if stage=='revalidating':clock=s['entry_at']-44
    elif stage=='confirmed':patch['entry_validation']['status']='confirmada'
    elif stage=='late':patch['telegram']['lead_status']='late'
    elif stage=='cancelled':patch.update(state='cancelado',result='CANCELADO',settlement_pending=False,
                                        cancellation_reason='Região original invalidada · teste sintético')
    elif stage=='apuring':
        clock=s['expiry_at']+2;patch.update(state='encerrado');patch['entry_validation']['status']='confirmada'
    elif stage!='pending':raise HTTPException(422,'Unknown fixture stage')
    service.clock=lambda:clock
    service.store.patch(s['id'],patch,'synthetic-stage',clock)
    return {'stage':stage}


@app.post('/_test/seed_review_history')
async def seed_review_history():
    service=app.state.service;now=time.time()
    for i in range(35):
        entry=int((now-(i+1)*86400)//300)*300;win=i<18 if i<30 else i%2==0
        price=101 if win else 99;ratio=.5 if i<30 else .618;sid='review-fixture-'+str(i)
        s={'id':sid,'asset':'GBP/USD','source_key':'deriv','source_identity':feed.identity,'source':feed.name,
           'strategy':'scan_all','strategy_resolved':'fibonacci','direction':'CALL','timeframe':'M5',
           'duration_seconds':300,'created_at':entry-60,'entry_at':entry,'expiry_at':entry+300,
           'state':'encerrado','result':'WIN' if win else 'LOSS','settlement_pending':False,
           'settled_at':entry+365,'entry_price':100,'expiry_price':price,'telegram':{'status':'enviado'},
           'settlement_source_identity':feed.identity,'settlement_entry_at':entry,'settlement_expiry_at':entry+300,
           'settlement_evidence':[Candle(entry,100,101,99,100).json(),Candle(entry+300,price,price+1,price-1,price).json()],
           'setup_contract':{'retracement_ratio':ratio,'levels':{}},
           'evidence':{'rules_version':RULES_VERSION,'pipeline_version':PIPELINE_VERSION,'regime':'tendencial',
                       'configuration':{'id':'ui-test-history-fixed'}}}
        service.store.add_signal(s,sid)
    return {'synthetic_records':35}
