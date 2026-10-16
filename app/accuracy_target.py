"""Predeclared descriptive assessment of a 90% goal; never a live signal gate."""
from collections import defaultdict
from .learning import lower_bound
from .outcome_evidence import research_outcome
from .validation_metrics import GROUP_FIELDS, canonical, result_of, summarize, _bootstrap, _day

POLICY = {
    'version': '1.26.0-target-1', 'target_pct': 90,
    'minimum_resolved': 100, 'minimum_resolved_days': 20,
    'minimum_nonoverlapping': 100, 'minimum_availability_pct': 95,
    'minimum_weeks': 2, 'minimum_week_sample': 20,
    'minimum_week_rate_pct': 90, 'confidence_pct': 95,
    'selectors': ['reference_fixed', 'regime_router', 'history_frozen',
                  'regime_structure_router', 'htf_regime_router'],
    'automatic_promotion': False,
    'inference': 'descriptive; no multiplicity correction or guarantee of future performance',
    'required_phases': ['validation', 'test'],
}


def assessment(rows, *, policy, cutoff, complete):
    """Count only knowledge available at the phase cutoff; retain failed labels."""
    checked = []
    excluded = 0
    for original in rows:
        row = original
        if (row.get('settled_at') or 0) > cutoff:
            row = {**row, 'state': 'pending_result', 'result': None, 'settlement_pending': True}
        elif result_of(row) in ('WIN', 'LOSS', 'EMPATE') and not research_outcome(row, cutoff):
            excluded += 1
            row = {**row, 'state': 'unavailable', 'result': 'INDISPONIVEL'}
        checked.append(row)
    stats = summarize(checked)
    denominator = stats['signals'] - stats['cancelled']
    available = stats['evaluated'] + stats['ties']
    availability = 100 * available / denominator if denominator else None
    days = defaultdict(lambda: [0, 0])
    nonoverlap = []; last_end = -1
    for row in sorted(checked, key=lambda r: (r['entry_at'], r['id'])):
        outcome = result_of(row)
        if outcome not in ('WIN', 'LOSS'):
            continue
        days[_day(row['entry_at'])][0] += outcome == 'WIN'
        days[_day(row['entry_at'])][1] += 1
        if row['entry_at'] >= last_end:
            nonoverlap.append(row); last_end = row['expiry_at']
    blocks = _bootstrap(days, rounded=False)
    n = stats['evaluated']; wins = stats['wins']
    lower = 100 * lower_bound(wins, n) if n else None
    nonoverlap_lower = 100 * lower_bound(sum(result_of(r) == 'WIN' for r in nonoverlap), len(nonoverlap)) if nonoverlap else None
    weeks = [w for w in stats['weeks'] if w['evaluated'] >= policy['minimum_week_sample']]
    expectation_ok = not stats['expectation_sample'] or stats['expected_roi_per_operation'] > 0
    checks = {
        'phase_complete': complete,
        'resolved_sample': n >= policy['minimum_resolved'],
        'resolved_days': stats['resolved_days'] >= policy['minimum_resolved_days'],
        'availability': availability is not None and availability >= policy['minimum_availability_pct'],
        'observed_rate': n > 0 and 100 * wins / n >= policy['target_pct'],
        'wilson_lower': lower is not None and lower >= policy['target_pct'],
        'day_block_lower': blocks is not None and blocks[0] >= policy['target_pct'],
        'nonoverlap_sample': len(nonoverlap) >= policy['minimum_nonoverlapping'],
        'nonoverlap_lower': nonoverlap_lower is not None and nonoverlap_lower >= policy['target_pct'],
        'weekly_stability': len(weeks) >= policy['minimum_weeks'] and all(100*w['wins']/w['evaluated'] >= policy['minimum_week_rate_pct'] for w in weeks),
        'known_payout_expectation': expectation_ok,
    }
    passed = all(checks.values())
    status = ('no_data' if not rows else 'collecting' if not complete else
              'phase_criteria_met' if passed else 'insufficient' if not checks['resolved_sample'] or not checks['resolved_days'] else 'criteria_not_met')
    return {**stats, 'target_pct': policy['target_pct'], 'status': status, 'checks': checks,
            'failed_checks': [k for k, v in checks.items() if not v],
            'availability_pct': round(availability, 2) if availability is not None else None,
            'availability_denominator': 'accepted decisions minus cancelled; includes pending and unproven outcomes',
            'unproven_outcomes': excluded,
            'nonoverlap_wilson_lower_pct': round(nonoverlap_lower, 2) if nonoverlap_lower is not None else None,
            'automatic_promotion': False, 'probability_estimate': False,
            'note': 'Criteria for this descriptive phase only; validation AND untouched final test are required. Not broker confirmation or a future guarantee.'}


def phase_groups(rows, *, policy, cutoff, complete):
    buckets = defaultdict(list)
    for row in rows:
        if row.get('selector') not in policy['selectors']:
            continue
        row = canonical(row)
        buckets[tuple(row.get(k, 'unknown') for k in GROUP_FIELDS)].append(row)
    return [{**dict(zip(GROUP_FIELDS, key)), **assessment(values, policy=policy, cutoff=cutoff, complete=complete)}
            for key, values in sorted(buckets.items(), key=lambda item: repr(item[0]))]
