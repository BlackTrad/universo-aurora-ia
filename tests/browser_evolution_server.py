"""Synthetic rate fixture, never imported by the production app."""
from copy import deepcopy
from tests.browser_release_server import app, public_sources
from app.selection_observer import config_snapshot
from app.learning import RULES_VERSION, PIPELINE_VERSION
from app.explanations import build_explanation
from app.market import Candle


@app.post('/_test/seed_evolution')
async def seed_evolution():
    await public_sources()
    service=app.state.service;now=service.clock();minute=int(now//60)*60
    config=config_snapshot(service.config,{**service.settings(),'provider':'coinbase','assets':['BTC/USD']})
    base={'asset':'BTC/USD','source_key':'coinbase','source':'Coinbase','source_identity':'coinbase:test',
          'strategy':'scan_all','strategy_resolved':'cci','timeframe':'M5','duration_seconds':300,
          'direction':'CALL','score':95,'reasons':['TESTE SINTÉTICO: confirmação fechada (70 pts)'],
          'result_kind':'indicativo','broker_confirmed':False,'telegram':{'status':'enviado'},
          'evidence':{'rules_version':RULES_VERSION,'pipeline_version':PIPELINE_VERSION,'regime':'lateral','configuration':config}}
    for i in range(30):
        t=minute-(31-i)*600;won=i<18
        s={**deepcopy(base),'id':'rate-'+str(i),'created_at':t-90,'entry_at':t,'expiry_at':t+300,'settled_at':t+365,
           'state':'encerrado','settlement_pending':False,'result':'WIN' if won else 'LOSS',
           'entry_price':100,'expiry_price':101 if won else 99,'settlement_source_identity':'coinbase:test',
           'settlement_entry_at':t,'settlement_expiry_at':t+300}
        price=s['expiry_price']
        s['settlement_evidence']=[Candle(t,100,101,99,100).json(),Candle(t+300,price,102,98,price).json()]
        service.store.add_signal(s,s['id'])
    s={**deepcopy(base),'id':'current-rate','created_at':now,'emitted_at':now,'entry_at':minute+600,'expiry_at':minute+900,
       'state':'aguardando entrada','result':'Resultado indisponível','settlement_pending':True}
    s['observed_win_rate']=service.store.win_rate_snapshot(s,now)
    s['engine_explanation']=build_explanation(s)
    service.store.add_signal(s,s['id'])
    return {'signal':s,'synthetic':True}


@app.post('/_test/resolve_evolution')
async def resolve_evolution():
    service=app.state.service;s=service.store.signal('current-rate');service.clock=lambda:s['expiry_at']+65
    service.store.patch(s['id'],{'state':'encerrado','settlement_pending':False,'result':'WIN','entry_price':100,
        'expiry_price':101,'settled_at':service.clock(),'settlement_source_identity':s['source_identity'],
        'settlement_entry_at':s['entry_at'],'settlement_expiry_at':s['expiry_at'],
        'settlement_evidence':[Candle(s['entry_at'],100,101,99,100).json(),Candle(s['expiry_at'],101,102,100,101).json()]},'test result',service.clock())
    return {'signal':service.store.signal(s['id'])}
