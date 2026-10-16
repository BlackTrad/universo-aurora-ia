"""Local controlled failure modes; never imported by the production application."""
import asyncio
from tests.browser_release_server import app,feed

_original=feed.candles

@app.post('/_test/slow_preview')
async def slow_preview():
    await app.state.service.stop_scan()
    async def slow(*args,**kwargs):
        await asyncio.Future()
    feed.candles=slow
    return {'synthetic':True,'mode':'slow'}

@app.post('/_test/restore_feed')
async def restore_feed():
    await app.state.service.stop_scan();feed.candles=_original
    return {'synthetic':True,'mode':'restored'}

@app.get('/_test/audit_status')
async def audit_status():
    service=app.state.service
    return {'synthetic':True,'telegram_calls':service.telegram.calls,'signals':len(service.store.signals()),
            'analysis_active':service.analysis_active}
