"""Compact analysis projection. It never selects, authorizes or emits signals."""


def decision_summary(*, audit, candidate, reason, decision_id, source_identity, data_at, received_at):
    """Capture diagnostics before any outcome is known, without extra queries."""
    candidates = audit.get('candidates', [])
    return {
        'decision_id': decision_id, 'source_identity': source_identity,
        'data_at': data_at, 'evaluated_at': received_at,
        'reason': reason, 'selected': bool(candidate),
        'candidate_count': len(candidates),
        'eligible_count': len(audit.get('eligible_candidates', candidates)),
        'evaluated_engines': len(audit.get('evaluations', [])),
        'pool_complete': audit.get('pool_complete', True),
        'detected_strategies': [c.get('strategy_resolved') for c in candidates if c.get('strategy_resolved')],
    }


def asset_previews(analysis):
    """One row per requested asset; legacy analyses use their original checks.

    Diagnostics and ranking are deliberately separate. A rejected candidate
    cannot become a trading opportunity through this display projection.
    """
    checks = {c['asset']: c for c in analysis.get('checks', []) if c.get('asset')}
    ranking = {r['asset']: r for r in analysis.get('ranking', []) if r.get('asset')}
    summaries = analysis.get('asset_assessments', {})
    finished = analysis.get('finished_at') is not None or analysis.get('status') in ('interrompida', 'erro')
    emitted = analysis.get('emitted_assets', {})
    config = analysis.get('configuration', {}).get('settings', {})
    duration = analysis.get('duration_seconds', config.get('duration_seconds'))
    rows = []
    for asset in analysis.get('assets', []):
        summary = summaries.get(asset, {})
        check = checks.get(asset)
        candidate = ranking.get(asset)
        status, reason = 'waiting', 'Aguardando avaliação deste ativo.'
        if summary:
            status = 'candidate' if summary.get('selected') else 'blocked' if summary.get('candidate_count') else 'no_setup'
            reason = summary.get('reason') or 'Candidato técnico identificado; emissão ainda não confirmada.'
        elif candidate:
            status, reason = 'candidate', 'Candidato técnico identificado.'
        elif not finished and analysis.get('current_asset') == asset:
            status, reason = 'analyzing', 'Consultando e verificando dados deste ativo.'
        if check:
            kind = check.get('kind', 'no_setup')
            status = {
                'no_setup': 'blocked' if summary.get('candidate_count') else 'no_setup',
                'data_error': 'market_closed' if check.get('code') == 'market_closed' else 'data_error',
                'engine_error': 'engine_error', 'discarded': 'discarded',
            }.get(kind, 'blocked')
            reason = check.get('reason') or 'Entrada bloqueada nesta análise.'
        elif asset in emitted:
            status, reason = 'emitted', 'Sinal registrado; a entrega ao Telegram é acompanhada no sinal.'
        elif candidate and finished:
            if analysis.get('preview'):
                status, reason = 'candidate', 'Prévia técnica; não emite sinal. Use Iniciar para monitorar.'
            else:
                status, reason = 'not_emitted', 'Candidato considerado, mas não emitido nesta análise.'
        if finished and analysis.get('status') == 'interrompida' and status in ('waiting', 'analyzing', 'candidate'):
            status, reason = 'interrupted', 'Análise interrompida; nenhuma nova entrada autorizada.'
        elif finished and analysis.get('status') == 'erro' and status in ('waiting', 'analyzing'):
            status, reason = 'engine_error', analysis.get('message') or 'Não foi possível concluir a análise.'
        rows.append({
            'asset': asset, 'source_key': analysis.get('source_key'),
            'timeframe': analysis.get('timeframe', 'M5'), 'duration_seconds': duration,
            'analysis_id': analysis['id'], 'status': status, 'reason': reason,
            'code': check.get('code') if check else None,
            'data_at': summary.get('data_at', (candidate or {}).get('data_at')),
            'evaluated_at': summary.get('evaluated_at'),
            'source_identity': summary.get('source_identity'),
            'decision_id': summary.get('decision_id'),
            'candidate_count': summary.get('candidate_count', int(bool(candidate))),
            'eligible_count': summary.get('eligible_count'),
            'evaluated_engines': summary.get('evaluated_engines'),
            'detected_strategies': summary.get('detected_strategies', []),
            'candidate': candidate if status in ('candidate', 'emitted') else None,
            'signal_id': emitted.get(asset),
        })
    return rows
