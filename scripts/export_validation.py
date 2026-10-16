"""Read-only validation export, including an explicit optional final-test opening."""
import argparse
import json
import sqlite3
import sys
import threading
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.store import Store
from app.selection_observer import SelectionObserver


def report(database,selection_database=None,open_final_test=False):
    source=Path(database).resolve()
    if not source.is_file():raise ValueError('Banco operacional não encontrado.')
    # Do not create tables or modify source databases while exporting.
    store=Store.__new__(Store);store.lock=threading.RLock()
    store.db=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True);store.db.row_factory=sqlite3.Row
    now=time.time()
    try:
        if store.db.execute("SELECT 1 FROM sqlite_master WHERE name='manual_confirmations'").fetchone():
            result=store.validation_report(now)
        else:
            from app.validation_metrics import grouped
            result={'operational_indicative':grouped(store.all_signals()),'manual_declared':[],
                    'legacy_note':'Schema anterior à coleta prospectiva; seleção/candles indisponíveis não são reconstruídos.'}
    finally:store.db.close()
    path=Path(selection_database) if selection_database else source.with_suffix('.selection.db')
    if path.is_file():
        observer=SelectionObserver.__new__(SelectionObserver)
        observer.lock=threading.RLock();observer._report_cache={}
        observer.db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
        try:result['observation']=observer.report(now,include_final_test=open_final_test)
        finally:observer.db.close()
    else:result['observation']={'status':'no selection database','sessions':[]}
    result['report_at']=now
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database');parser.add_argument('output')
    parser.add_argument('--selection-database');parser.add_argument('--open-final-test',action='store_true')
    args=parser.parse_args()
    try:
        payload=report(args.database,args.selection_database,args.open_final_test)
        with Path(args.output).open('x',encoding='utf-8') as target:json.dump(payload,target,ensure_ascii=False,indent=2,allow_nan=False)
        print('Relatório salvo. Sem ativação automática de seletores.')
    except (ValueError,OSError,sqlite3.Error) as error:parser.exit(1,str(error)+'\n')
