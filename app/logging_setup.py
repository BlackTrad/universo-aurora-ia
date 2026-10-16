"""Bounded JSONL diagnostics. Never log requests, credentials or raw exceptions."""
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone, timedelta
from pathlib import Path
import traceback
import re
import uuid
import time
import asyncio
from contextvars import ContextVar
from functools import wraps
from .version import VERSION
BRASILIA = timezone(timedelta(hours=-3), name='Brasilia')

CONTEXT=ContextVar('aurora_log_context',default={})
SESSION_ID=uuid.uuid4().hex
SECRETS=set()

def register_secrets(*values):
    SECRETS.update(str(v) for v in values if v)

def sanitize(value):
    if isinstance(value,dict):
        return {str(k):('[OCULTO]' if any(x in str(k).lower() for x in ('token','secret','password','authorization','chat_id','cookie','api_key')) else sanitize(v)) for k,v in value.items()}
    if isinstance(value,(list,tuple)):return [sanitize(v) for v in value]
    if isinstance(value,str):
        for secret in sorted(SECRETS,key=len,reverse=True):
            if re.fullmatch(r'-?[0-9]+',secret):
                # A numeric chat ID is a whole value, not every matching digit
                # inside a version, timestamp, symbol or another identifier.
                value=re.sub(r'(?<![\w.+-])'+re.escape(secret)+r'(?![\w.+-])','[OCULTO]',value)
            else:
                value=value.replace(secret,'[OCULTO]')
        value=re.sub(r'\b\d{6,}:[A-Za-z0-9_-]{20,}', '[TOKEN OCULTO]',value)
        value=re.sub(r'https?://\S+', '[URL OCULTA]',value)
    return value

def traced(name):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args,**kwargs):
            started=time.monotonic()
            trace_token=CONTEXT.set({**CONTEXT.get(),'request_trace':uuid.uuid4().hex})
            event(name+'.started')
            try:
                result=await fn(*args,**kwargs)
            except asyncio.CancelledError:
                event(name+'.cancelled')
                CONTEXT.reset(trace_token)
                raise
            except Exception as error:
                event(name+'.failed',level=logging.ERROR,exc_info=True,code=getattr(error,'code',None),duration_ms=round((time.monotonic()-started)*1000,2),detail=str(error) if hasattr(error,'code') else None)
                CONTEXT.reset(trace_token)
                raise
            event(name+'.finished',duration_ms=round((time.monotonic()-started)*1000,2),
                  **({k:result[k] for k in ('status','detail','retry_after') if k in result} if isinstance(result,dict) else {}))
            CONTEXT.reset(trace_token)
            return result
        return wrapped
    return decorate

class SafeFormatter(logging.Formatter):
    def format(self, record):
        data = {'at_utc': datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                'at_brasilia': datetime.fromtimestamp(record.created,BRASILIA).isoformat(),
                'session_id':SESSION_ID,'project':'AURORA','version':VERSION,
                'level': record.levelname,
                'component':record.name.removeprefix('aurora.') if record.name!='aurora' else record.getMessage().split('.')[0],
                'event': record.getMessage()}
        data.update(getattr(record, 'details', {}))
        if record.exc_info and record.exc_info[0]:
            data['error_type'] = record.exc_info[0].__name__
            data['frames'] = [{'file': Path(f.filename).name, 'function': f.name, 'line': f.lineno}
                              for f in traceback.extract_tb(record.exc_info[2])]
        return json.dumps(sanitize(data), ensure_ascii=False, default=str)

def configure(directory=None, name='aurora', filename='aurora.log'):
    directory = Path(directory or Path(__file__).resolve().parents[1]/'logs')
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    target = str((directory/filename).resolve())
    if not any(getattr(h, 'baseFilename', None) == target for h in logger.handlers):
        for handler in logger.handlers[:]:
            logger.removeHandler(handler); handler.close()
        handler = RotatingFileHandler(target, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8')
        handler.setFormatter(SafeFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger

# Only selected metadata goes to disk; no payload, message, URL, header or token.
FIELDS = {'analysis_id','signal_id','provider','strategy','resolved','asset','direction',
          'status','code','kind','duration_ms','entry_at','expiry_at','method','route',
          'http_status','request_id','attempt','result','state','exit_code','stage','count','timeframe','reason','detail','score','reasons','assets','duration_seconds',
          'next_run_at','cycles','first_candle','last_candle','age_seconds','volume_available',
          'candles','candidate','operation','symbol','granularity','end','response_count',
          'scan_id','request_trace','retry_after','entry_price','expiry_price','settlement_rule',
          'learning','candidates','evaluated_assets','failed_assets','telegram_configured','runtime','system','checks',
          'limits','rules_version','pipeline_version','quality','audit','source_identity','regime','trace'}

def event(name, level=logging.INFO, exc_info=False, **fields):
    logging.getLogger('aurora').log(level, name, exc_info=exc_info,
        extra={'details': sanitize({k: v for k, v in {**CONTEXT.get(),**fields}.items() if k in FIELDS})})
