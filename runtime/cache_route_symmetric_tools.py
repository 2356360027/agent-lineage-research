"""Three symmetric tool opportunities; local environment, not API approval.

Keep completed experiments' implementation untouched. Reuse its synchronous
read-only backend, charge-before-execution journal and terminal sealing. The
initializer below deliberately does not initialize a legacy price/profile and
then mutate it: the very first receipt must describe the actual protocol.
Cached results are trusted operator inputs, not proven correct by their hashes.
The eventual batch admission must bind them to the reviewed source snapshot.
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import sys

from runtime import cache_intent_tools as base

VERSION = 'cache-route-symmetric-tools-v1'
PROFILE = 'symmetric-60-three-attempts-v1'
BUDGET, MAX_ATTEMPTS = 60, 3


class ToolSession(base.ToolSession):
    def __init__(self, *, database_path, database_sha256, history_sql,
                 cached_result, input_receipt, output, materialized_available,
                 cache_read_cost):
        if (type(materialized_available) is not bool or
                type(cache_read_cost) is not int or cache_read_cost not in (1, 20)):
            raise ValueError('Boolean availability and fixed price 1 or 20 required')
        if (type(database_sha256) is not str or
                re.fullmatch(r'[0-9a-f]{64}', database_sha256) is None):
            raise ValueError('Invalid database fingerprint')
        if type(history_sql) is not str or not history_sql.strip():
            raise ValueError('Missing historical SQL')
        base.valid_result(cached_result)
        if cached_result.get('sql_sha256') != base.audit.digest(history_sql):
            raise ValueError('Cache not bound to historical SQL')
        if type(input_receipt) is not dict:
            raise ValueError('Operator input receipt required')
        base.audit.encoded(input_receipt)  # Fail before creating output.
        self.database, self.expected_db = Path(database_path), database_sha256
        self._check_database()
        self.output = Path(output)
        base.absent(self.output)
        self.available, self.cache_cost = materialized_available, cache_read_cost
        self.budget_profile, self.initial_budget = PROFILE, BUDGET
        self.nonbinding_for_configured_horizon = True
        self.max_attempts = MAX_ATTEMPTS
        self.balance, self.attempts, self.sql_calls = BUDGET, 0, 0
        self.closed, self.invalidated = False, False
        self.cache = deepcopy(cached_result)
        self.output.mkdir(parents=True, exist_ok=False)
        try:
            base.audit.save_new(self.output / 'started.json', {
                'version': VERSION, 'base_version': base.VERSION,
                'created_at': datetime.now(timezone.utc).isoformat(),
                'purpose': 'TOOL_ENVIRONMENT_ONLY_NO_INFERENCE_APPROVAL',
                'configuration': self.describe(), 'input_receipt': deepcopy(input_receipt),
                'history_sql_sha256': base.audit.digest(history_sql),
                'cache_rows_sha256': cached_result['sequence_sha256'],
                'source_sha256': base.audit.hash_file(__file__),
                'base_source_sha256': base.audit.hash_file(base.__file__),
                'readonly_source_sha256': base.audit.hash_file(base.audit.__file__),
                'python': sys.version, 'limits': base.audit.LIMITS,
                'inference_eligible': False})
            for source, name in (( __file__, 'tool_source.py'),
                                 (base.__file__, 'base_tool_source.py'),
                                 (base.audit.__file__, 'readonly_source.py')):
                shutil.copyfile(source, self.output / name)
        except BaseException:
            self.invalidated = True
            raise  # Preserve partial startup; never reconstruct or resume it.

    def describe(self):
        return super().describe() | {'last_attempt_sql_only': False}

    # Inherited _classify has SQL-only gates exclusively for the named legacy
    # recovery profiles; PROFILE is deliberately distinct. Tests dispatch both
    # tools on the third opportunity, not just inspect this description.
