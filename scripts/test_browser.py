"""Controlled browser suite, with explicit environment and retained exits."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess

def main():
    parser=argparse.ArgumentParser()
    for option in ('project','python','node','chromium','playwright','out'):
        parser.add_argument('--'+option,required=True,type=Path)
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    environment={**os.environ,'AURORA_TEST_ROOT':str(args.project.resolve()),
        'AURORA_TEST_PYTHON':str(args.python.absolute()),'CHROMIUM_PATH':str(args.chromium.resolve()),
        'PLAYWRIGHT_MODULE':str(args.playwright.resolve()),'AURORA_TEST_ARTIFACT_DIR':str((args.out/'screenshots').resolve())}
    package=json.loads((args.project/'tests/package.json').read_text())
    names=[part.strip().removeprefix('node ') for part in package['scripts']['browser'].split('&&')]
    results=[]
    for name in names:
        target=args.out/(Path(name).stem+'.txt')
        with target.open('w') as log:
            try:
                completed=subprocess.run([str(args.node.resolve()),name],cwd=args.project/'tests',env=environment,
                    stdout=log,stderr=subprocess.STDOUT,timeout=120)
                code=completed.returncode
            except subprocess.TimeoutExpired:code=124
        passed=code==0 and bool(re.search(r'(?m)^PASS[:\s]',target.read_text()))
        results.append({'script':name,'exit_code':code,'pass_marker':passed,'log':target.name})
        (args.out/'browser-summary.json').write_text(json.dumps(results,indent=2)+'\n')
        print(name,'PASS' if passed else 'FAIL',flush=True)
    raise SystemExit(0 if all(r['pass_marker'] for r in results) else 1)

if __name__=='__main__':main()
