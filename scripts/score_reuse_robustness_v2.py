"""Audit-only compatibility correction; never alters frozen runs or calls models.

Frozen v1 audit passed the whole llama.cpp usage dictionary to Counter.update;
prompt_tokens_details is nested, not an additive integer. Restrict the usage
Counter to the three validated top-level integer totals. Label-list counters
retain original behavior. All inference, labels, denominators and prompts stay
in the original frozen module. Freeze/check this adapter's SHA before use.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UsageCompatibleCounter(Counter):
    def update(self, iterable=None, **kwds):
        if isinstance(iterable, dict) and 'prompt_tokens' in iterable:
            iterable = {k: iterable[k] for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')}
            if any(type(v) is not int or v < 0 for v in iterable.values()):
                raise ValueError('Invalid usage totals')
            if iterable['total_tokens'] != iterable['prompt_tokens']+iterable['completion_tokens']:
                raise ValueError('Usage totals disagree')
        return super().update(iterable, **kwds)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--manifest-sha256', required=True)
    p.add_argument('--scorer-sha256', required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args(); prepared = a.prepared.resolve()
    if file_hash(Path(__file__)) != a.scorer_sha256: raise ValueError('Analysis adapter changed')
    sys.path.insert(0, str(prepared/'source'))
    original = prepared/'source/scripts/run_reuse_robustness.py'
    spec = importlib.util.spec_from_file_location('frozen_robustness', original)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    module.Counter = UsageCompatibleCounter
    base_save = module.save
    repair = {'kind': 'AUDIT_ONLY_USAGE_METADATA_COMPATIBILITY',
        'adapter_sha256': a.scorer_sha256, 'original_runner_sha256': file_hash(original),
        'reason': 'v1 Counter.update failed on nested prompt_tokens_details; not a model failure',
        'changed_inference_prompts_or_semantic_scoring': False, 'new_generations': 0,
        'usage_fields_aggregated': ['prompt_tokens', 'completion_tokens', 'total_tokens']}
    def annotated_save(path, value):
        if path.name == 'summary.json': value = dict(value, analysis_repair=repair)
        return base_save(path, value)
    module.save = annotated_save
    module.audit(prepared, a.manifest_sha256, a.output)


if __name__ == '__main__': main()
