"""Bounded, causal observation history from already validated scanner batches.

It never expands the operational indicator window or makes another API request.
Closed candle revisions disable this context until the conflict ages out.
"""
from collections import OrderedDict


class ContextWindow:
    def __init__(self, max_candles=240, max_instruments=32):
        self.max_candles=max_candles
        self.max_instruments=max_instruments
        self.windows=OrderedDict()

    def remember(self, key, rows, interval, cutoff):
        window=self.windows.setdefault(key,{'candles':{},'conflicts':{}})
        self.windows.move_to_end(key)
        for c in rows:
            if c.t+interval>cutoff:continue
            old=window['candles'].get(c.t)
            if old and old[0]!=c:
                window['conflicts'].setdefault(c.t,cutoff)
                continue  # Never replace the first version already available.
            if old is None:window['candles'][c.t]=(c,cutoff)
        keep=set(sorted(window['candles'])[-self.max_candles:])
        window['candles']={t:v for t,v in window['candles'].items() if t in keep}
        window['conflicts']={t:at for t,at in window['conflicts'].items() if t in keep}
        while len(self.windows)>self.max_instruments:self.windows.popitem(last=False)

    def snapshot(self, key, cutoff):
        window=self.windows.get(key)
        if not window:return [],{'status':'cold_start','additional_api_calls':0}
        available={t:v for t,v in window['candles'].items() if v[1]<=cutoff}
        if any(t in available and at<=cutoff for t,at in window['conflicts'].items()):
            return [],{'status':'closed_candle_revision','additional_api_calls':0}
        rows=[v[0] for t,v in sorted(available.items())]
        return rows,{'status':'collected','additional_api_calls':0,'base_candles':len(rows),
                     'first_at':rows[0].t if rows else None,'last_at':rows[-1].t if rows else None,
                     'cutoff':cutoff,'restart_policy':'warmup_from_new_validated_batches'}
