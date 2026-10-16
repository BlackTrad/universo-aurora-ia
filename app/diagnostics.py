"""Portable diagnostics containing only structured, sanitized application logs."""
import io
import json
import logging
import platform
import zipfile
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from .logging_setup import sanitize, event
from .version import VERSION


def build_archive(directory, snapshot=None):
    for handler in logging.getLogger('aurora').handlers:handler.flush()
    target=io.BytesIO()
    skipped=0
    files=0
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(Path(directory).glob('*.log*')):
            if not path.is_file() or path.is_symlink():continue
            # Read only bounded application logs, not arbitrary files or credentials.
            if path.name not in {f'{name}.log{suffix}' for name in ('aurora','startup','study') for suffix in ('','.1','.2','.3','.4','.5')}:continue
            lines=[]
            with path.open(encoding='utf8',errors='replace') as source:
                for line in source:
                    try:row=json.loads(line)
                    except ValueError:skipped+=1;continue
                    if not isinstance(row,dict):skipped+=1;continue
                    lines.append(json.dumps(sanitize(row),ensure_ascii=False))
            archive.writestr('logs/'+path.name,'\n'.join(lines)+'\n');files+=1
        archive.writestr('diagnostico.json',json.dumps(sanitize({
            'project':'AURORA','version':VERSION,
            'generated_at':datetime.now(ZoneInfo('America/Sao_Paulo')).isoformat(),
            'python':platform.python_version(),'system':platform.system(),
            'log_files':files,'incomplete_lines_skipped':skipped,'snapshot':snapshot,
        }),ensure_ascii=False,indent=2))
    event('diagnostics.exported',count=files)
    return target.getvalue()
