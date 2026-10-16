"""Descriptive metrics. Scores and empirical rates are never win probabilities."""
from collections import defaultdict
from datetime import datetime, timezone
import math
import random
from .learning import lower_bound

OUTCOMES=('WIN','LOSS','EMPATE')
GROUP_FIELDS=('source_key','source_identity','strategy','asset','timeframe','duration_seconds',
              'rules_version','pipeline_version','selector','selector_version','configuration_id','regime','direction','broker','apex_setup','duration_basis')


def result_of(row):
    if row.get('state') in ('cancelado','cancelled'):
        return 'CANCELADO'
    if row.get('settlement_pending') or row.get('state') in ('aguardando entrada','em andamento','pending_validation','pending_result'):
        return 'PENDENTE'
    outcome=row.get('result')
    if outcome in OUTCOMES:
        return outcome
    if row.get('state') in ('encerrado','settled','unavailable'):
        return 'INDISPONIVEL'
    return 'PENDENTE'


def canonical(signal):
    evidence=signal.get('evidence',{})
    trace=evidence.get('decision_trace',{})
    configuration=evidence.get('configuration',{})
    return {**signal,'strategy':signal.get('strategy_resolved',signal.get('strategy','unknown')),
            'rules_version':evidence.get('rules_version') or signal.get('rules_version') or 'não informada',
            'pipeline_version':evidence.get('pipeline_version') or signal.get('pipeline_version') or 'não informada',
            'selector':signal.get('selector',configuration.get('selector','legacy_unknown')),
            'selector_version':signal.get('selector_version',configuration.get('selector_version','unknown')),
            'configuration_id':signal.get('configuration_id',configuration.get('id','legacy_unknown')),
            'regime':evidence.get('regime',trace.get('regime',signal.get('regime','unknown'))),
            'broker':signal.get('broker','fonte externa'),
            'apex_setup':signal.get('apex_setup') or 'base',
            'duration_basis':signal.get('duration_basis','indicative_planned')}


def _day(t):
    return datetime.fromtimestamp(t,timezone.utc).date().isoformat()


def _bootstrap(days, *, rounded=True):
    # A whole UTC day is the resampling unit, including simultaneous/correlated assets.
    values=list(days.values())
    if len(values)<20:
        return None
    rng=random.Random(1240); rates=[]
    for _ in range(500):
        blocks=[rng.choice(values) for _ in values]
        wins=sum(w for w,n in blocks); n=sum(n for w,n in blocks)
        if n:rates.append(100*wins/n)
    rates.sort()
    interval=[rates[int(.025*(len(rates)-1))],rates[int(.975*(len(rates)-1))]] if rates else None
    return [round(v,2) for v in interval] if interval and rounded else interval


def summarize(rows, observation_start=None, observation_end=None):
    rows=sorted(rows,key=lambda r:(r.get('entry_at',r.get('created_at',0)),r.get('id','')))
    counts={k:0 for k in (*OUTCOMES,'PENDENTE','INDISPONIVEL','CANCELADO')}
    days=defaultdict(lambda:[0,0]); weeks=defaultdict(lambda:{'wins':0,'losses':0,'ties':0,'unavailable':0,'pending':0,'cancelled':0})
    loss_run=maximum=clusters=0;cluster_end=-1;last_end=-1;nonoverlap=[];expected=[];payout_origins=set()
    for row in rows:
        outcome=result_of(row);counts[outcome]+=1
        t=row.get('entry_at',row.get('created_at',0));end=row.get('expiry_at',t)
        if outcome!='CANCELADO':
            if t>=cluster_end:clusters+=1
            cluster_end=max(cluster_end,end)
        if outcome in ('WIN','LOSS'):
            loss_run=loss_run+1 if outcome=='LOSS' else 0
            maximum=max(maximum,loss_run)
            days[_day(t)][0]+=outcome=='WIN';days[_day(t)][1]+=1
            if t>=last_end:
                nonoverlap.append(row);last_end=end
        elif outcome!='EMPATE':
            loss_run=0  # Unknown outcomes interrupt a demonstrable loss sequence.
        iso=datetime.fromtimestamp(t,timezone.utc).isocalendar();week=f'{iso.year}-W{iso.week:02}'
        bucket=weeks[week]
        bucket[{'WIN':'wins','LOSS':'losses','EMPATE':'ties','INDISPONIVEL':'unavailable','PENDENTE':'pending','CANCELADO':'cancelled'}[outcome]]+=1
        payout=row.get('payout_pct')
        if outcome in OUTCOMES and type(payout) in (int,float) and math.isfinite(payout) and 0<payout<=100:
            expected.append(payout/100 if outcome=='WIN' else -1 if outcome=='LOSS' else 0)
            payout_origins.add(row.get('payout_kind','user_declared'))
    sample=counts['WIN']+counts['LOSS'];closed=sample+counts['EMPATE']+counts['INDISPONIVEL']
    first=min((r.get('created_at',r.get('entry_at',0)) for r in rows),default=None)
    last=max((r.get('expiry_at',r.get('entry_at',0)) for r in rows),default=None)
    start=observation_start if observation_start is not None else first
    end=observation_end if observation_end is not None else last
    hours=(end-start)/3600 if start is not None and end is not None and end>start else None
    rate=100*counts['WIN']/sample if sample else None
    return {'signals':len(rows),'wins':counts['WIN'],'losses':counts['LOSS'],'ties':counts['EMPATE'],
            'pending':counts['PENDENTE'],'unavailable':counts['INDISPONIVEL'],'cancelled':counts['CANCELADO'],
            'evaluated':sample,'rate_denominator':'WIN + LOSS; excludes ties, pending, unavailable and cancelled',
            'observed_rate_pct':round(rate,2) if rate is not None else None,
            'win_rate_pct':round(rate,1) if sample>=30 else None,
            'wilson_95_pct':[round(100*lower_bound(counts['WIN'],sample),2),round(100*(1-lower_bound(counts['LOSS'],sample)),2)] if sample else None,
            'uncertainty_note':'Wilson is descriptive under independence; overlap/correlation can make it optimistic. Day blocks also cannot prove independence.',
            'day_block_95_pct':_bootstrap(days),'resolved_days':len(days),'overlap_clusters':clusters,
            'nonoverlapping_sample':len(nonoverlap),'nonoverlapping_win_rate_pct':round(100*sum(result_of(r)=='WIN' for r in nonoverlap)/len(nonoverlap),2) if nonoverlap else None,
            'first_signal_at':first,'last_expiry_at':last,'observation_start':start,'observation_end':end,
            'frequency_per_hour':round(len(rows)/hours,4) if hours else None,
            'frequency_scope':'explicit observation window' if observation_start is not None else 'first generated signal to last expiry; excludes silent edges',
            'max_loss_streak':maximum,'loss_streak_order':'entry_at, id; ties preserve streak, unknown/cancelled break it; not result-arrival order',
            'unavailable_pct':round(100*counts['INDISPONIVEL']/closed,2) if closed else None,
            'unavailable_denominator':'WIN + LOSS + EMPATE + unavailable',
            'expected_roi_per_operation':sum(expected)/len(expected) if expected else None,
            'expectation_sample':len(expected),'payout_origins':sorted(payout_origins),
            'financial_kind':'estimated on known-payout subset; tie assumed zero net return' if expected else 'not calculated: no known payout',
            'weeks':[{'week_utc':week,**item,'evaluated':item['wins']+item['losses'],
                      'observed_rate_pct':round(100*item['wins']/(item['wins']+item['losses']),2) if item['wins']+item['losses'] else None}
                     for week,item in sorted(weeks.items())],
            'evidence_status':'insufficient' if sample<100 or len(days)<20 else 'descriptive_only_not_promoted'}


def grouped(rows, observation_start=None, observation_end=None):
    buckets=defaultdict(list)
    for row in rows:
        row=canonical(row);buckets[tuple(row.get(k,'unknown') for k in GROUP_FIELDS)].append(row)
    return [{**dict(zip(GROUP_FIELDS,key)),**summarize(values,observation_start,observation_end)}
            for key,values in sorted(buckets.items(),key=lambda kv:repr(kv[0]))]


def compare_selectors(rows,left='reference_fixed',right='regime_router'):
    technical=[r for r in rows if r.get('selector') in (left,right) and r.get('accepted')]
    selected=[r for r in technical if result_of(r)!='CANCELADO']
    def decision(r):return (r.get('session_id'),r.get('source_key','unknown'),r['source_identity'],r['asset'],r['timeframe'],r['duration_seconds'],r['entry_at'],r.get('duration_basis','indicative_planned'))
    def movement(r):return (*decision(r),r['direction'])
    sets={name:{movement(r):r for r in selected if r['selector']==name} for name in (left,right)}
    a,b=sets[left],sets[right];common=a.keys()&b.keys();left_only=a.keys()-b.keys();right_only=b.keys()-a.keys()
    paired=[(a[k],b[k]) for k in sorted(common,key=repr) if result_of(a[k]) in ('WIN','LOSS') and result_of(b[k]) in ('WIN','LOSS')]
    blocks=defaultdict(list)
    for x,y in paired:blocks[_day(x['entry_at'])].append(int(result_of(y)=='WIN')-int(result_of(x)=='WIN'))
    ci=None
    if len(blocks)>=20:
        rng=random.Random(1240);values=list(blocks.values());estimates=[]
        for _ in range(500):
            sample=[v for _ in values for v in rng.choice(values)]
            estimates.append(100*sum(sample)/len(sample))
        estimates.sort();ci=[estimates[12],estimates[487]]
    decision_sets={name:{decision(r):r for r in selected if r['selector']==name} for name in (left,right)}
    da,db=decision_sets[left],decision_sets[right];shared=da.keys()&db.keys()
    decision_pairs=[(da[k],db[k]) for k in sorted(shared,key=repr)
                    if result_of(da[k]) in ('WIN','LOSS') and result_of(db[k]) in ('WIN','LOSS')]
    paired_blocks=defaultdict(list)
    for x,y in decision_pairs:
        paired_blocks[_day(x['entry_at'])].append(int(result_of(y)=='WIN')-int(result_of(x)=='WIN'))
    decision_ci=None
    if len(paired_blocks)>=20:
        rng=random.Random(1268);values=list(paired_blocks.values());estimates=[]
        for _ in range(500):
            sample=[v for _ in values for v in rng.choice(values)]
            estimates.append(100*sum(sample)/len(sample))
        estimates.sort();decision_ci=[estimates[12],estimates[487]]
    duplicate_count=sum(sum(r['selector']==name for r in selected)-len(decision_sets[name]) for name in (left,right))
    common_divergences=sum(result_of(a[k])!=result_of(b[k]) for k in common
                          if result_of(a[k]) in OUTCOMES and result_of(b[k]) in OUTCOMES)
    comparable=duplicate_count==0 and common_divergences==0
    difference=100*sum(int(result_of(y)=='WIN')-int(result_of(x)=='WIN') for x,y in decision_pairs)/len(decision_pairs) if decision_pairs else None
    return {'left':left,'right':right,'common_signals':len(common),'paired_resolved':len(paired),
            'common_left':summarize([a[k] for k in common]),'common_right':summarize([b[k] for k in common]),
            'exclusive_left':summarize([a[k] for k in left_only]),'exclusive_right':summarize([b[k] for k in right_only]),
            'paired_difference_pp':100*sum(int(result_of(y)=='WIN')-int(result_of(x)=='WIN') for x,y in paired)/len(paired) if paired and comparable else None,
            'paired_day_block_95_pp':ci if comparable else None,
            'cancelled_left':sum(r['selector']==left and result_of(r)=='CANCELADO' for r in technical),
            'cancelled_right':sum(r['selector']==right and result_of(r)=='CANCELADO' for r in technical),
            'both_selected_decisions':len(shared),
            'opposing_direction_decisions':sum(da[k]['direction']!=db[k]['direction'] for k in shared),
            'decision_paired_resolved':len(decision_pairs),
            'decision_paired_difference_pp':difference if comparable else None,
            'decision_paired_day_block_95_pp':decision_ci if comparable else None,
            'comparison_valid':comparable,'duplicate_decision_identities':duplicate_count,
            'common_outcome_divergences':common_divergences,
            'note':'Common = same session/provider/source/asset/timeframe/duration/entry/timing basis/direction. Closed and intrabar decisions stay separate. Same-decision pairs include opposing directions; cancelled decisions are reported separately. Exclusives are different opportunity sets, not a paired comparison. Same-direction outcome divergence or duplicate identities invalidates the paired decision estimate.'}
