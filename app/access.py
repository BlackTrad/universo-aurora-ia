"""Explicit spectator response contracts; private operator state stays server-side."""

VIEWER_READS = frozenset(('/api/auth/status', '/api/state', '/api/instruments',
                         '/api/quote', '/api/chart', '/api/history', '/api/history/days'))
VIEWER_PAGES = frozenset(('/bot', '/analise', '/sinais', '/historico', '/universo-ia'))


def pick(value, keys):
    return {key:value[key] for key in keys if key in value} if isinstance(value,dict) else {}


def viewer_vote(value):
    return pick(value, ('agente','status','direcao','confianca','justificativa',
                        'dados_insuficientes','latency_ms'))


def viewer_ai(value):
    result=pick(value, ('status','detail','updated_at'))
    result['context']=pick(value.get('context'), ('ativo','timeframe','fonte'))
    result['last_decision']=pick(value.get('last_decision'), ('direcao','confianca','motivo')) or None
    result['last_votes']=[viewer_vote(vote) for vote in value.get('last_votes',[])]
    result['agents']=[pick(agent, ('agente','status','detail','model')) for agent in value.get('agents',[])]
    return result


def viewer_signal(value):
    if not isinstance(value,dict):return None
    result=pick(value, ('id','asset','direction','timeframe','duration_seconds','created_at',
        'emitted_at','entry_at','expiry_at','state','result','entry_price','expiry_price',
        'settlement_pending','source','source_key','source_identity','strategy','strategy_resolved',
        'score','cancellation_reason','manual_intrabar','reasons'))
    result['observed_win_rate']=pick(value.get('observed_win_rate'),
        ('kind','basis','sample','rate_pct','wilson_95_pct'))
    result['entry_timing']=pick(value.get('entry_timing'), ('phase','label'))
    result['engine_explanation']=pick(value.get('engine_explanation'), ('source','text'))
    result['telegram']=pick(value.get('telegram'), ('status','lead_status'))
    result['telegram_result']=pick(value.get('telegram_result'), ('status',))
    consensus=value.get('ai_consensus') or {}
    result['ai_consensus']={'approved':consensus.get('approved',False),
        'decision':pick(consensus.get('decision'), ('direcao','confianca','motivo')),
        'votes':[viewer_vote(vote) for vote in consensus.get('votes',[])]}
    annotation=value.get('chart_annotations')
    if isinstance(annotation,dict):
        result['chart_annotations']=pick(annotation, ('signal_id','source_key','asset','source_identity',
            'timeframe','direction','entry_at','expiry_at','cancelled','basis','entry_price_inferred'))
        result['chart_annotations']['levels']=[pick(row, ('id','label','price','selected')) for row in annotation.get('levels',[])]
        result['chart_annotations']['zones']=[pick(row, ('id','label','lower','upper','start_at','end_at')) for row in annotation.get('zones',[])]
    return result


def viewer_snapshot(value):
    result=pick(value, ('version','server_time','timezone','provider','source_mode','strategy',
                       'source','available_assets','asset_sources','timeframes','analysis_active','busy'))
    result['settings']=pick(value.get('settings'), ('assets','timeframe','duration_seconds','strategy','source_mode'))
    result['providers']=[pick(row, ('key','name')) for row in value.get('providers',[])]
    result['feed']=pick(value.get('feed'), ('status','detail','data_at','max_age'))
    result['scan']=pick(value.get('scan'), ('id','active','preview','message','next_run_at'))
    result['signals']=[viewer_signal(row) for row in value.get('signals',[])]
    result['current_signal']=viewer_signal(value.get('current_signal'))
    result['ai']=viewer_ai(value.get('ai') or {})
    result['analyses']=[pick(row, ('id','scan_id','status','message','assets','timeframe',
        'duration_seconds','strategy','source_key','created_at','finished_at','updated_at','preview'))
        for row in value.get('analyses',[])]
    return result
