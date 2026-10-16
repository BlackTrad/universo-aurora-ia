"""Controlled audit fixture; no real prices, trades or messages."""
import json
from tests.browser_release_server import app
from tests.test_accuracy_1260 import observed
from app.selection_observer import SelectionObserver, config_snapshot


@app.post('/_test/seed_accuracy')
async def seed_accuracy():
    service=app.state.service
    service.observer=SelectionObserver(':memory:')
    now=service.clock();minute=int(now//60)*60
    session=service.observer.session(config_snapshot(service.config,service.settings()),minute-20*86400)
    for i in range(10):
        row={**observed(i,start=minute-3*86400),'session_id':session['id'],
             'configuration_id':session['id'],'sent_to_telegram':False,'result_kind':'experimental_indicativo'}
        with service.observer.db:
            service.observer.db.execute('INSERT INTO observations VALUES(?,?,?)',(row['id'],row['created_at'],json.dumps(row)))
    return {'synthetic':True,'session':session['id']}
