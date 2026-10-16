"""Servidor EXCLUSIVO de teste visual. Nunca usar como ambiente operacional."""
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tests.test_system import Feed,TG,fixture
from app.market import Candle
from app.main import create_app
from app.config import Config
class VisualFeed(Feed):
    async def candles(self,asset,interval=300,count=100):
        rows=fixture();last=int(time.time()//interval)*interval-interval
        return [Candle(last-(len(rows)-1-i)*interval,c.o,c.h,c.l,c.c) for i,c in enumerate(rows)]
feed=VisualFeed()
feed.name='TESTE SINTÉTICO · sem valor operacional'
app=create_app(Config(admin_token='local-validation-token-not-a-secret',database=':memory:'),feed,TG())
