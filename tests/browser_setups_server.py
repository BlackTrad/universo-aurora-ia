"""Synthetic fixtures for the WIN board, isolated from production and Telegram."""
from copy import deepcopy
from tests.browser_release_server import app, public_sources
from tests.test_evolution_1250 import signal, manual


@app.post('/_test/seed_setups')
async def seed_setups():
    await public_sources()
    service=app.state.service;now=service.clock();minute=int(now//60)*60
    i=0
    for strategy,source,wins,losses in [('cci','deriv',35,15),('cci','coinbase',32,8),
        ('bb_reversion','deriv',30,10),('ema_pullback','deriv',7,3),
        ('fibonacci','deriv',6,0),('stochastic','deriv',5,1)]:
        for j in range(wins+losses):
            entry=minute-(200-i)*600
            row=signal(i,'WIN' if j<wins else 'LOSS',strategy_resolved=strategy,
                source_key=source,source_identity=source+':test',asset='BTC/USD' if source=='coinbase' else 'EUR/USD',
                created_at=entry-90,entry_at=entry,expiry_at=entry+300,settled_at=entry+365,
                settlement_source_identity=source+':test',settlement_entry_at=entry,settlement_expiry_at=entry+300,
                telegram={'status':'enviado','at':entry-80})
            service.store.add_signal(row,str(i))
            if strategy=='bb_reversion' and j==0:service.store.confirm_manual(row['id'],manual(result='WIN',broker='Corretora teste'),now)
            i+=1
    # This record must not add a WIN. Its original result remains untouched.
    invalid={**deepcopy(row),'id':'no-proof','strategy_resolved':'cci','result':'WIN',
             'settlement_source_identity':'wrong-source','expiry_price':101}
    service.store.add_signal(invalid,'no-proof')
    # A different version is selectable, never merged with the current rules.
    old={**deepcopy(row),'id':'old-rule','strategy_resolved':'cci','result':'WIN','expiry_price':101}
    old['settlement_evidence']=[{**q,'o':101,'h':102,'l':100,'c':101} if i==1 else q for i,q in enumerate(old['settlement_evidence'])]
    old['evidence']['pipeline_version']='old-test-version'
    service.store.add_signal(old,'old-rule')
    return {'status':'seeded','synthetic':True}


@app.post('/_test/add_setup_win')
async def add_setup_win():
    service=app.state.service;now=service.clock();entry=int(now//60)*60-600
    row=signal(999,'WIN',id='additional-win',source_identity='deriv:test',
        created_at=entry-90,entry_at=entry,expiry_at=entry+300,settled_at=entry+365,
        settlement_source_identity='deriv:test',settlement_entry_at=entry,settlement_expiry_at=entry+300)
    service.store.add_signal(row,'additional-win')
    return {'synthetic':True}
