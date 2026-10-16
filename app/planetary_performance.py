"""Read-only, causal audit of the pre-signal -> planetary decision -> settlement funnel.

A verified indicative quote outcome is NOT evidence of an executed broker trade.
No learning weights, selection thresholds, operational settings or signals are changed.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from math import isfinite
from statistics import median
from typing import Any

from .outcome_evidence import comparable_outcome

VERSION = '3.7.0-observational-1'


def _numeric(value: Any) -> bool:
    return type(value) in (int, float) and isfinite(value)


def _percent(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 2) if denominator else None


def _quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return round(values[lower] + (values[upper] - values[lower]) * (position - lower), 2)


def _vote_is_valid(vote: Any) -> bool:
    if not isinstance(vote, dict):
        return False
    return (vote.get('status') == 'disponivel'
            and vote.get('dados_insuficientes') is False
            and vote.get('direcao') in ('CALL', 'PUT', 'NEUTRO')
            and _numeric(vote.get('confianca'))
            and 0 <= vote['confianca'] <= 100)


def summarize_planetary(decisions: list[dict], signals: list[dict], *, now: float,
                        source_count: int | None = None) -> dict:
    """All timestamps are UTC seconds; only evidence available by `now` is used.

    `decisions` carries items from Store.decisions (decision + timestamped events).
    Rejected pre-signals have no observed settlement; they must NOT be given an
    imputed WIN or LOSS, even if another asset eventually moved in that direction.
    """
    if not _numeric(now) or now <= 0:
        raise ValueError('Invalid audit cutoff')
    signals_by_decision = {s['decision_id']: s for s in signals
                           if isinstance(s, dict) and isinstance(s.get('decision_id'), str)
                           and _numeric(s.get('created_at')) and s['created_at'] <= now}
    all_rows = []
    agent_data = defaultdict(lambda: {'calls': 0, 'available': 0, 'timeouts': 0,
                                      'rate_limits': 0, 'unavailable': 0, 'errors': 0,
                                      'call': 0, 'put': 0, 'neutral': 0, 'cache_hits': 0, 'latencies_ms': [],
                                      'verified_hypothetical_votes': 0, 'matching_price_direction': 0})
    strategies = defaultdict(lambda: {'decisions': 0, 'candidates': 0, 'ai_reviewed': 0,
                                      'ai_approved': 0, 'ai_rejected': 0, 'verified_published': 0})
    reasons = Counter()
    accepted = reviewed = approved = rejected = no_setup = proven = unproven = 0
    eligible_holdout = []
    all_decisions = 0
    for item in decisions:
        if not isinstance(item, dict):
            continue
        d = item.get('decision')
        if not isinstance(d, dict) or not _numeric(d.get('created_at')) or d['created_at'] > now:
            continue
        all_decisions += 1
        sel = d.get('selection') or {}
        candidate = sel.get('selected') if isinstance(sel, dict) else None
        candidate = candidate if isinstance(candidate, dict) else None
        strategy = (candidate or {}).get('strategy_resolved') or (candidate or {}).get('strategy')
        if not isinstance(strategy, str) or not strategy.strip():
            conf = d.get('configuration') or {}
            strategy = conf.get('strategy') if isinstance(conf, dict) else None
        strategy = strategy if isinstance(strategy, str) and strategy else 'indefinida'
        stats = strategies[strategy]
        stats['decisions'] += 1
        if candidate:
            accepted += 1
            stats['candidates'] += 1
        else:
            no_setup += 1
        events = [e for e in (item.get('events') or []) if isinstance(e, dict)
                  and _numeric(e.get('at')) and d['created_at'] <= e['at'] <= now]
        ai_events = [e for e in events if e.get('event') == 'ai_consensus']
        if not ai_events:
            continue
        # At most one vote per named agent and one consensus per decision.
        ai = ai_events[0]
        reviewed += 1
        stats['ai_reviewed'] += 1
        ai_approved = ai.get('approved') is True
        approved += ai_approved
        rejected += not ai_approved
        stats['ai_approved' if ai_approved else 'ai_rejected'] += 1
        if not ai_approved:
            reason = (ai.get('decision') or {}).get('motivo')
            reasons[reason if isinstance(reason, str) and reason else 'consenso_indisponivel'] += 1
        signal = signals_by_decision.get(d.get('id'))
        verified = bool(signal and comparable_outcome(signal, now))
        if signal:
            if verified:
                proven += 1
                stats['verified_published'] += 1
                # Holdout is descriptive only; both predictors refer to a *published*
                # signal. Other technical-only candidates have no valid settlement.
                eligible_holdout.append({'created_at': d['created_at'], 'entry_at': signal['entry_at'],
                    'settled_at': signal['settled_at'], 'result': signal['result'],
                    'technical_direction': candidate.get('direction') if candidate else None,
                    'ai_direction': (ai.get('decision') or {}).get('direcao')})
            else:
                unproven += 1
        seen = set()
        for vote in ai.get('votes') or []:
            if not isinstance(vote, dict):
                continue
            name = vote.get('agente')
            if not isinstance(name, str) or not name.strip() or name in seen:
                continue
            seen.add(name)
            row = agent_data[name]
            row['calls'] += 1
            if vote.get('cache_hit') is True:
                row['cache_hits'] += 1
            latency = vote.get('latency_ms')
            if vote.get('cache_hit') is not True and _numeric(latency) and 0 <= latency <= 300000:
                row['latencies_ms'].append(float(latency))
            status = vote.get('status')
            if status == 'timeout':
                row['timeouts'] += 1
            elif status == 'rate_limit':
                row['rate_limits'] += 1
            elif status == 'indisponivel':
                row['unavailable'] += 1
            elif not _vote_is_valid(vote):
                row['errors'] += 1
            else:
                row['available'] += 1
                direction = vote['direcao']
                row[{'CALL': 'call', 'PUT': 'put', 'NEUTRO': 'neutral'}[direction]] += 1
                # Alternate-agent evaluation is hypothetical and conditional on a
                # published original signal, not an independently executed trade.
                if verified and direction in ('CALL', 'PUT'):
                    row['verified_hypothetical_votes'] += 1
                    rose = signal['expiry_price'] > signal['entry_price']
                    falls = signal['expiry_price'] < signal['entry_price']
                    row['matching_price_direction'] += (direction == 'CALL' and rose) or (direction == 'PUT' and falls)
    agents = []
    for name, value in sorted(agent_data.items()):
        samples = value.pop('latencies_ms')
        agents.append({'name': name, **value, 'availability_pct': _percent(value['available'], value['calls']),
                       'latency_median_ms': round(median(samples), 2) if samples else None,
                       'latency_p95_ms': _quantile(samples, .95), 'latency_samples': len(samples),
                       'hypothetical_direction_match_pct': _percent(value['matching_price_direction'],
                                                                  value['verified_hypothetical_votes'])})
    # Chronological descriptive holdout: no feedback weight/threshold is fitted.
    # At the holdout boundary exclude train outcomes settled after its start.
    eligible_holdout.sort(key=lambda r: (r['created_at'], r['entry_at']))
    boundary = int(len(eligible_holdout) * .7)
    holdout_start = eligible_holdout[boundary]['created_at'] if 0 < boundary < len(eligible_holdout) else None
    train = [r for r in eligible_holdout[:boundary] if r['settled_at'] <= holdout_start] if holdout_start else []
    test = eligible_holdout[boundary:] if holdout_start else []
    def _win_rate(rows):
        comparable = [r for r in rows if r['result'] in ('WIN', 'LOSS')]
        return {'n': len(comparable), 'wins': sum(r['result'] == 'WIN' for r in comparable),
                'observed_win_pct': _percent(sum(r['result'] == 'WIN' for r in comparable), len(comparable))}
    return {'version': VERSION, 'as_of_utc': now, 'scope': 'read_only_observational',
            'decisions_examined': all_decisions,
            'source_decision_count': source_count if source_count is not None else all_decisions,
            'sample_truncated': source_count is not None and source_count > len(decisions),
            'technical_candidates': accepted, 'no_technical_setup': no_setup,
            'ai_reviewed': reviewed, 'ai_approved': approved, 'ai_rejected': rejected,
            'ai_rejection_reasons': [{'code': k, 'count': v} for k, v in reasons.most_common()],
            'published_with_price_proof': proven, 'published_without_price_proof': unproven,
            'agents': agents, 'strategies': [{'strategy': k, **v} for k, v in sorted(strategies.items())],
            'holdout': {'method': 'chronological_70_30_descriptive_no_fitting',
                        'start_utc': holdout_start, 'causally_available_train': _win_rate(train),
                        'holdout_published': _win_rate(test),
                        'selection_bias': 'Only emitted signals have settlement; rejected opportunities cannot be scored.',
                        'comparison_status': 'no_causal_motor_vs_ai_uplift_estimate',
                        'reason': 'Technical and AI agree by construction for emitted signals; the original-only counterfactual is unobserved.'},
            'disclaimer': 'Original-source price verification is indicative, NOT broker execution or confirmed contract settlement. Agent votes on published signals are hypothetical, not trades. No win probability or performance gain is inferred.'}
