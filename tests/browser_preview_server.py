"""Controlled per-asset preview server; never imported in production."""
from fastapi import HTTPException
from app.market import DataError
from app.strategies import STRATEGIES, scan_all
from app.strategies import scan_all as evaluate
from tests.browser_release_server import app, feed

_original=dict(STRATEGIES)
_candles=feed.candles


@app.post('/_test/preview_mode')
async def preview_mode(mode:str='none'):
    service=app.state.service
    await service.stop_scan()
    if mode not in ('none','blocked','qualified','unavailable'):raise HTTPException(422,'Unknown controlled mode')
    STRATEGIES.clear()
    STRATEGIES.update({key:(lambda _: (None,'Nenhum setup confirmado no teste controlado.'))
                       for key in _original if key not in ('auto','scan_all')})
    STRATEGIES['scan_all']=scan_all
    if mode=='blocked':
        def oscillator(rows):
            return {'direction':'CALL','score':95,'setup_candle':rows[-1].t,
                    'strategy_resolved':'cci','reasons':['Teste controlado de grupo único']},''
        STRATEGIES['pa_hammer']=lambda rows: ({'direction':'CALL','score':80,'setup_candle':rows[-1].t,'manual':True,'mandatory_criteria':[{'criterion':'Região confirmada','passed':False}], 'strategy_resolved':'pa_hammer','reasons':['Teste controlado de critério obrigatório incompleto']},'')
    elif mode=='qualified':
        STRATEGIES['scan_all']=evaluate
        for key in _original:
            if key.startswith('pa_'):STRATEGIES[key]=_original[key]
    async def candles(*args,**kwargs):
        if mode=='unavailable':raise DataError('Fonte indisponível no teste controlado.','network')
        return await _candles(*args,**kwargs)
    feed.candles=candles
    return {'mode':mode,'synthetic':True}
