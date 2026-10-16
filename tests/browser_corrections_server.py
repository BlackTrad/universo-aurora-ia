"""New UI regressions use synthetic records; no broker or Telegram calls."""
from tests.browser_release_server import app, public_sources
from tests.test_evolution_1250 import signal, manual


@app.post('/_test/seed_corrections')
async def seed_corrections():
    await public_sources()
    service=app.state.service;now=service.clock();minute=int(now//60)*60
    entry=minute-1200
    for i,proof in enumerate((True,False)):
        row=signal(i,created_at=entry-90,entry_at=entry,expiry_at=entry+300,settled_at=entry+365,
            settlement_entry_at=entry,settlement_expiry_at=entry+300)
        if not proof:row['settlement_evidence']=None
        service.store.add_signal(row,str(i))
        entry+=600
    service.store.confirm_manual('0',{**manual(result='WIN'),'entry_at':minute-1200,'expiry_at':minute-1136.5},now)
    current=signal(100,id='late-ack',created_at=now,emitted_at=now,entry_at=minute+600,expiry_at=minute+900,
        state='aguardando entrada',settlement_pending=True,result='Resultado indisponível',
        telegram={'status':'enviado','lead_status':'late','actionable_at_ack':False,'message_id':999})
    service.store.add_signal(current,'late-ack')
    return {'synthetic':True,'manual_duration_seconds':63.5}
