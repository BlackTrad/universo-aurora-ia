"""Native tick transport and temporal diagnostics from uploaded production logs."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from app.config import Config
from app.deriv import DerivProvider
from app.live_price import PricePoint, validate_point
from app.market import DataError
from app.price_stream import PriceStreams
from app.service import Service
from app.store import Store
from tests.test_entry_revision_12613 import fixture_signal, EntryFeed, close_service
from tests.test_system import TG


class Socket:
    def __init__(self,response):self.response=response;self.sent=[];self.closed=False
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.closed=True
    async def send(self,data):self.sent.append(json.loads(data))
    async def recv(self):return json.dumps(self.response)


@pytest.mark.parametrize('bad',[None,'symbol','echo','missing','time_bool','time_nan','price','message_type','request_id'])
def test_deriv_snapshot_uses_first_native_tick_and_always_closes_its_subscription(bad):
    async def run():
        response={'msg_type':'tick','req_id':1,'echo_req':{'ticks':'frxEURUSD','subscribe':1},
                  'tick':{'symbol':'frxEURUSD','epoch':999,'quote':'100.2'}}
        if bad=='symbol':response['tick']['symbol']='frxGBPUSD'
        elif bad=='echo':response['echo_req']['ticks']='frxGBPUSD'
        elif bad=='missing':response['tick'].pop('epoch')
        elif bad=='time_bool':response['tick']['epoch']=True
        elif bad=='time_nan':response['tick']['epoch']=float('nan')
        elif bad=='price':response['tick']['quote']='NaN'
        elif bad=='message_type':response['msg_type']='history'
        elif bad=='request_id':response['req_id']=2
        socket=Socket(response);provider=DerivProvider(Config())
        with patch('app.deriv.connect',return_value=socket),patch('app.deriv.time.time',return_value=1000):
            if bad:
                with pytest.raises(DataError):await provider.live_price('EUR/USD')
            else:
                point=await provider.live_price('EUR/USD')
                assert (point.price,point.at,point.received_at,point.asset,point.source_identity)==(100.2,999,1000,'EUR/USD',provider.identity)
                validate_point(point,asset='EUR/USD',identity=provider.identity,now=1000)
        assert socket.sent==[{'ticks':'frxEURUSD','subscribe':1,'req_id':1}]
        assert socket.closed
    asyncio.run(run())


def test_provider_rejection_records_only_bounded_native_error_codes():
    async def run():
        socket=Socket({'error':{'code':'InputValidationFailed','message':'private response text'}});events=[]
        with patch('app.deriv.connect',return_value=socket),patch('app.deriv.event',side_effect=lambda name,**data:events.append((name,data))):
            with pytest.raises(DataError) as error:await DerivProvider(Config()).live_price('EUR/USD')
        assert error.value.code=='provider_rejected' and socket.closed
        rejected=[data for name,data in events if name=='deriv.request.rejected']
        assert rejected==[{'operation':'tick','provider_codes':['InputValidationFailed']}]
        assert 'private response text' not in str(events)
    asyncio.run(run())


@pytest.mark.parametrize('source,received,now,code',[(1000.001,1000,1000,'live_price_future'),
                                                   (1000,1000.001,1000,'live_price_future'),
                                                   (989,1000,1000,'live_price_stale')])
def test_temporal_diagnostics_never_relax_future_or_stale_price_validation(source,received,now,code):
    point=PricePoint(100,source,received,'EUR/USD','native')
    with pytest.raises(DataError) as error:validate_point(point,asset='EUR/USD',identity='native',now=now)
    assert error.value.code==code
    timing=error.value.price_timing
    assert timing['source_at']==source and timing['received_at']==received and timing['checked_at']==now
    assert timing['source_minus_receipt_seconds']==round(source-received,6)


def test_stream_interruption_retains_timestamps_and_never_delivers_the_future_quote():
    async def run():
        gap=asyncio.Event();hold=asyncio.Event();seen=[]
        class Provider:
            identity='native'
            async def stream_prices(self,assets):
                yield PricePoint(100,1000.001,1000,assets[0],self.identity)
        async def on_point(*args):seen.append(args)
        async def on_gap(*args):gap.set()
        async def sleep(*args):await hold.wait()
        streams=PriceStreams({'deriv':Provider()},lambda:1000,on_point,on_gap,sleep=sleep)
        try:
            await streams.sync({('deriv','EUR/USD')});await asyncio.wait_for(gap.wait(),2)
            state=deepcopy(streams.status[('deriv','EUR/USD')])
            assert not seen and not streams.latest and state['code']=='live_price_future'
            assert state['price_timing']['source_minus_receipt_seconds']==.001
        finally:await streams.close()
    asyncio.run(run())


def test_signal_cancellation_keeps_stream_clock_evidence_without_reopening_the_setup():
    async def run():
        signal=fixture_signal();clock=[signal['entry_at']-40];store=Store(':memory:');store.add_signal(signal,'one')
        service=Service(Config(database=':memory:'),store,EntryFeed(signal,clock),TG(),clock=lambda:clock[0],automatic_only=True)
        timing={'source_at':clock[0]+.01,'received_at':clock[0],'checked_at':clock[0],'source_minus_receipt_seconds':.01}
        service.price_streams.status[('deriv','EUR/USD')]={'code':'live_price_future','price_timing':timing}
        try:
            await service.stream_gap(('deriv','EUR/USD'),'live_price_future')
            saved=store.signal(signal['id'])
            assert saved['state']=='cancelado' and saved['entry_validation']['price_timing']==timing
            assert saved['setup_contract']==signal['setup_contract'] and saved['entry_at']==signal['entry_at']
        finally:await close_service(service)
    asyncio.run(run())
