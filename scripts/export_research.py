"""Export prospective research read-only; never merge into operational history."""
import argparse
from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.validate_experiments import metrics,hour_exposure
from app.outcome_evidence import research_metric_view


def metric_rows(rows, now):
    """Verify labels in a read-only view; export original observations unchanged."""
    projected=[research_metric_view(row,now) for row in rows]
    return [{**row,'result':'INDISPONIVEL' if row['state']=='unavailable' else
             'CANCELADO' if row['state']=='cancelled' else row.get('result')}
            for row in projected if row.get('accepted') and row['state'] in ('settled','cancelled','unavailable')]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    uri=args.database.resolve().as_uri()+'?mode=ro'
    with sqlite3.connect(uri,uri=True) as db:
        rows=[json.loads(row[0]) for row in db.execute('SELECT payload FROM observations ORDER BY created,id')]
    groups=defaultdict(list);now=time.time()
    for row in rows:
        groups[(row['version'],row['source_identity'],row['asset'],row['strategy'],row['variant'],row['interval_seconds'],row['duration_seconds'],
                row.get('research_configuration',{}).get('code_sha256','legacy_unknown'))].append(row)
    summaries=[]
    for key,items in groups.items():
        views=[research_metric_view(row,now) for row in items]
        complete=metric_rows(items,now)
        start=min(r['created_at'] for r in items);end=max(r.get('settled_at') or r['observed_at'] for r in views)
        counters={'observations':len(items),'rejected':sum(r['state']=='rejected' for r in items),
                  'pending':sum(r['state'] in ('pending_validation','pending_result') for r in views),
                  'outcomes_without_quote_proof':sum(r.get('outcome_proof_status')=='invalid_or_missing_price_proof' for r in views)}
        summaries.append({'version':key[0],'source_identity':key[1],'asset':key[2],'strategy':key[3],'variant':key[4],
            'interval_seconds':key[5],'duration_seconds':key[6],'start':start,'end':end,**counters,
            'code_sha256':key[7],
            **metrics(complete,max(1,end-start)),
            'by_regime':{regime:metrics([r for r in complete if r.get('regime')==regime],max(1,end-start))
                         for regime in sorted({r.get('regime','indefinido') for r in items})},
            'by_hour_brasilia':{hour:metrics([r for r in complete if r.get('context',{}).get('hour_brasilia')==hour],max(1,hour_exposure(start,end)[hour]))
                                for hour in sorted({r.get('context',{}).get('hour_brasilia',-1) for r in items})}})
    args.output.mkdir(parents=True,exist_ok=False)
    (args.output/'observations.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    report={'mode':'prospectivo_experimental','broker_confirmed':False,'production_enabled':False,'groups':summaries,
        'note':'Observações não enviadas. Sem pausa/limites operacionais simulados na coleta; hipóteses avaliadas isoladamente. Taxas não validam rentabilidade.'}
    (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf8')
    print(str(args.output/'summary.json'))


if __name__=='__main__':main()
