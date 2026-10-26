"""Inspect only staged public artifacts; never enumerate ignored raw runs.

Produces a review inventory, not a network push. Blob bytes come from the Git
index, so unrelated working-tree edits cannot enter the publication snapshot.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(['git','-c','safe.directory='+ROOT.as_posix(),'-C',str(ROOT),*args])


def audit(base):
    changes=git('diff','--cached','--name-status','-z',base).decode('utf-8').split('\0')
    entries=[]
    forbidden=(b'-----BEGIN '+b'PRIVATE KEY-----',b'-----BEGIN '+b'OPENSSH PRIVATE KEY-----')
    for i in range(0,len(changes)-1,2):
        status,path=changes[i:i+2]
        if status not in ('A','M'):raise ValueError('Review deletions/renames separately')
        if not path.startswith(('configs/','scripts/','runtime/','tests/','reports/reuse_separation_14day/')):
            raise ValueError('Outside selected public scope: '+path)
        if path.startswith('runtime/runs/') or Path(path).suffix not in ('.py','.json','.txt','.svg','.pdf'):
            raise ValueError('Nonpublic artifact: '+path)
        data=git('show',':'+path)
        if len(data)>1000000:raise ValueError('Oversized unexpected public artifact')
        if any(k in data for k in forbidden) or re.search(rb'\b(?:sk-[A-Za-z0-9_-]{20,}|hf_[A-Za-z0-9]{20,})\b',data):
            raise ValueError('Credential-like material detected in '+path+'; contents not printed')
        sha=git('rev-parse',':'+path).decode().strip()
        entries.append({'path':path,'status':status,'blob_sha':sha,'bytes':len(data),
                        'sha256':hashlib.sha256(data).hexdigest(),'mode':'100644','type':'blob'})
    return {'base_commit':base,'base_tree':git('rev-parse',base+'^{tree}').decode().strip(),
        'staged_tree':git('write-tree').decode().strip(),'files':entries,
        'files_count':len(entries),'bytes':sum(e['bytes'] for e in entries),
        'raw_runs_included':False,'scope_and_credential_pattern_check_passed':True,
        'not_a_general_proof_of_absence_of_sensitive_data':True}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True)
    print(json.dumps(audit(p.parse_args().base),indent=2))
