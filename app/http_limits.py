"""Bound mutation bodies even when Content-Length is absent or incorrect."""
import asyncio
import time
from starlette.responses import Response

class BodyLimitMiddleware:
    def __init__(self,app,limit=8192):
        self.app,self.limit=app,limit

    async def __call__(self,scope,receive,send):
        if scope['type']!='http' or scope['method'] in ('GET','HEAD','OPTIONS'):
            return await self.app(scope,receive,send)
        chunks=[];size=0;deadline=time.monotonic()+10
        while True:
            try:message=await asyncio.wait_for(receive(),max(0,deadline-time.monotonic()))
            except asyncio.TimeoutError:
                return await Response('Tempo limite para receber a requisição.',408)(scope,receive,send)
            if message['type']=='http.disconnect':return
            body=message.get('body',b'');size+=len(body)
            if size>self.limit:
                return await Response('Requisição muito grande',413)(scope,receive,send)
            chunks.append(body)
            if not message.get('more_body',False):break
        delivered=False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered=True
                return {'type':'http.request','body':b''.join(chunks),'more_body':False}
            return await receive()
        await self.app(scope,replay,send)
