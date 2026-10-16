"""Compare every detector and both selectors on identical causal prefixes.

No fields are ignored. Direction,
technical score, reasons, selection, regime and rejection text must be equal.
This uses synthetic prices; it cannot demonstrate trading efficacy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys


def strip_geometry(value):
    return value


def evaluate(project, output):
    sys.path.insert(0,str(project.resolve()))
    from app.market import Candle
    from app.strategies import STRATEGIES
    cases=[]
    for name in ('call_fixture.json','breakout_only_fixture.json'):
        rows=[Candle(**item) for item in json.loads((project/'tests'/name).read_text())]
        cases.append((name,rows))
    for seed in (0,42,1085,21885):
        rng=random.Random(seed);rows=[];price=1.1
        for i in range(80):
            closing=price+rng.uniform(-.0003,.0003)
            rows.append(Candle(1800000000+i*300,price,max(price,closing)+rng.uniform(.0001,.0003),
                               min(price,closing)-rng.uniform(.0001,.0003),closing))
            price=closing
        cases.append(('seed-'+str(seed),rows))
    decisions=[]
    for name,rows in cases:
        for length in range(40,len(rows)+1):
            for key,evaluator in STRATEGIES.items():
                decisions.append([name,length,key,strip_geometry(evaluator(rows[:length]))])
    output.write_text(json.dumps(decisions,sort_keys=True,separators=(',',':'),ensure_ascii=False)+'\n')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--project',type=Path)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--baseline',type=Path);parser.add_argument('--current',type=Path)
    args=parser.parse_args()
    if args.project:
        evaluate(args.project,args.out);return
    paths=[]
    for label,path in [('baseline',args.baseline),('current',args.current)]:
        out=args.out.parent/(label+'-technical-decisions.json')
        subprocess.run([sys.executable,__file__,'--project',str(path),'--out',str(out)],check=True,
                       stdout=subprocess.DEVNULL)
        paths.append(out)
    before,after=[json.loads(path.read_text()) for path in paths]
    differences=[{'before':a,'after':b} for a,b in zip(before,after) if a!=b]
    report={'mode':'synthetic_causal_software_comparison','evaluations_per_version':len(before),
            'registry':list(dict.fromkeys(row[2] for row in before)),
            'ignored_metadata_fields':[],
            'baseline_sha256':hashlib.sha256(paths[0].read_bytes()).hexdigest(),
            'current_sha256':hashlib.sha256(paths[1].read_bytes()).hexdigest(),
            'equal':before==after,'differences':differences,
            'scope':'Detector predicates, scores, reasons and baseline selection only; technical rules and selection are unchanged in this software revision.',
            'market_efficacy_validated':False}
    args.out.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({key:value for key,value in report.items() if key!='differences'},indent=2))
    if not report['equal']:raise SystemExit(1)


if __name__=='__main__':main()
