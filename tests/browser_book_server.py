"""Public-price fixture server for browser QA, not used by production."""
from app.live_price import PricePoint
from tests.browser_release_server import app,feed

mode='stale'
price_calls=0


async def live_price(asset):
    global price_calls
    price_calls+=1;now=feed.clock()
    return PricePoint(100.25,now-20 if mode=='stale' else now,now,asset,feed.identity,str(price_calls))


feed.live_price=live_price


@app.post('/_test/book_mode')
async def book_mode(value:str='good'):
    global mode
    mode=value
    return {'synthetic':True,'mode':mode}


@app.post('/_test/poll_book')
async def poll_book():
    result=await app.state.service.poll_intrabar()
    return {'synthetic':True,'signals':[s['id'] for s in result],'price_calls':price_calls}
