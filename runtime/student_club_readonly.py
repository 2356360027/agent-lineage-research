"""Fixed column-scoped backend for the proposed student-club development batch.

Same policy for every protocol/price/availability cell. SQLite authorization
runs during statement preparation, before execution; output filtering is not
the privacy boundary. This is not an OS security sandbox or task correctness
checker. No API client or admission/fee authority exists in this module.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from runtime import bird_readonly_audit as audit
from runtime.cache_route_symmetric_tools import ToolSession as SymmetricSession

VERSION = 'student-club-column-scope-v1'


def policy():
    return {'event':['event_id','event_name','event_date'],
            'attendance':['link_to_event','link_to_member'],
            'member':['member_id','t_shirt_size']}


def execute(database, sql):
    if type(sql) is not str or not sql.strip():
        raise ValueError('SQL text required')
    result = audit.execute_readonly(database,sql,allowed_columns=policy())
    result['column_policy_version'] = VERSION
    result['column_policy_sha256'] = audit.digest(policy())
    return result


def run_child(database, sql):
    packet = {'database':str(Path(database).resolve()),'sql':sql}
    started=time.perf_counter()
    try:
        p=subprocess.run([sys.executable,'-B','-m','runtime.student_club_readonly'],
            input=audit.encoded(packet),capture_output=True,
            cwd=Path(__file__).resolve().parents[1],timeout=audit.LIMITS['hard_seconds'],
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    except subprocess.TimeoutExpired:
        return dict(status='HARD_TIMEOUT',complete=False,elapsed_seconds=time.perf_counter()-started)
    if p.returncode:
        return dict(status='WORKER_ERROR',complete=False,returncode=p.returncode,
                    stderr=p.stderr.decode('utf-8',errors='replace')[:10000])
    result=json.loads(p.stdout)
    if (result.get('column_policy_version')!=VERSION or
            result.get('column_policy_sha256')!=audit.digest(policy())):
        raise ValueError('Missing scoped worker receipt')
    result['process_wall_seconds']=time.perf_counter()-started
    return result


class ToolSession(SymmetricSession):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        try:
            audit.save_new(self.output/'column_policy.json',dict(version=VERSION,
                allowed_columns=policy(),scope='Same fixed allowlist across all cells',
                semantic_correctness_assessed=False))
            shutil.copyfile(__file__,self.output/'column_scoped_source.py')
        except BaseException:
            self.invalidated=True
            raise

    def describe(self):
        return super().describe() | {'column_policy_version':VERSION,
                                    'column_policy_sha256':audit.digest(policy())}

    def _execute_sql(self, sql):
        return run_child(self.database,sql)


def main():
    packet=json.loads(sys.stdin.buffer.read(audit.LIMITS['max_sql_bytes']*6+4096))
    if type(packet) is not dict or set(packet)!={'database','sql'}:
        raise ValueError('Worker accepts no policy, limit or price override')
    sys.stdout.buffer.write(audit.encoded(execute(packet['database'],packet['sql'])))


if __name__=='__main__':main()
