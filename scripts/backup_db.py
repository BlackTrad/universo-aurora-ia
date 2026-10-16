"""Consistent SQLite backup; refuses to overwrite an existing destination."""
import argparse
import sqlite3
from pathlib import Path

def backup(source,destination):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if not source.is_file():raise ValueError('Banco de origem não encontrado.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    # Reserve exclusively: never truncate a database or previous backup.
    with destination.open('xb'):pass
    try:
        src=sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)
        dst=sqlite3.connect(destination)
        try:
            src.backup(dst)
            if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('Backup não passou na verificação de integridade.')
        finally:dst.close();src.close()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source');parser.add_argument('destination');args=parser.parse_args()
    try:print('Backup verificado:',backup(args.source,args.destination))
    except (OSError,ValueError,sqlite3.Error) as error:parser.exit(1,'Backup não concluído: '+str(error)+'\n')
