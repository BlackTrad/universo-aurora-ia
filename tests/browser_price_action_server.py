"""Controlled UI fixture. Synthetic records never enter operational history."""
import json
from tests.browser_release_server import app
from tests.test_accuracy_1260 import observed
from tests.test_price_action_1268 import hypothesis, structural
from app.price_action_selection import SELECTOR, LABELS, select
from app.selection_observer import SelectionObserver, config_snapshot


@app.post('/_test/seed_price_action')
async def seed_price_action():
    service=app.state.service
    service.observer=SelectionObserver(':memory:')
    minute=int(service.clock()//60)*60
    session=service.observer.session(config_snapshot(service.config,service.settings()),minute-20*86400)
    rows=[]
    for i,key in enumerate(LABELS):
        base={**observed(i,start=minute-3*86400),'session_id':session['id'],
              'configuration_id':session['id'],'strategy':key,'timeframe':'M1','interval_seconds':60,
              'rules_version':'synthetic-research-only','sent_to_telegram':False,
              'result_kind':'experimental_indicativo','observed_at':minute-3*86400-90+i*600,
              'selector':'price_action:'+key}
        rows.append(base)
        if key=='structure_pullback':
            rows.extend([{**base,'id':'router-'+str(i),'selector':SELECTOR},
                         {**base,'id':'reference-'+str(i),'selector':'reference_fixed'}])
    # A false original WIN must be displayed as unavailable without altering it.
    rows[2]['settlement_evidence']=None
    decision=select([hypothesis('breakout_retest',now=minute),
                     hypothesis('breakout_retest','PUT',now=minute)],
                    structural('transicao'),now=minute,interval=60,min_lead=60)
    rows.append({**rows[0],'id':'latest-conflict','created_at':minute-2,'observed_at':minute-2,
                 'selector':SELECTOR,'accepted':False,'state':'rejected','result':None,
                 'entry_at':minute+120,'expiry_at':minute+420,'reason':decision['reason'],
                 'context_evidence':decision['context_evidence']})
    with service.observer.db:
        for row in rows:
            service.observer.db.execute('INSERT INTO observations VALUES(?,?,?)',
                                        (row['id'],row['created_at'],json.dumps(row)))
    return {'synthetic':True,'session':session['id']}
