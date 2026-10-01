"""Full-request audited descriptive competence gate; no population inference."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean

from .comprehension_holdout import grid, key_for, run_cell, validate
from .dependency_probe import check_server, sources
from .e1 import digest, save
from .probe_score import check, diagnostics
from .timing_score import AuditClient


def gate(overall, strata, symmetry, recoding, criteria):
    checks = {'overall_mae': overall['mae'] <= criteria['overall_mae_max']}
    for name, values in strata.items():
        for metric, limit, is_min in (
            ('mae', 'stratum_mae_max', False),
            ('direction', 'stratum_direction_min', True),
            ('probability_direction', 'stratum_probability_direction_min', True),
            ('inconsistent', 'stratum_inconsistency_max', False),
        ):
            checks[f'{name}_{metric}'] = (values[metric] >= criteria[limit] if is_min
                                         else values[metric] <= criteria[limit])
    for name, value in symmetry.items():
        checks[f'{name}_complement_symmetry'] = value <= criteria['complement_symmetry_max']
    checks['recoding_discrepancy'] = recoding <= criteria['recoding_discrepancy_max']
    return {'passed': all(checks.values()), 'checks': checks,
            'meaning': 'Operational finite-grid screen only; not proof of general competence or DICE efficacy'}


def summarize(root):
    dr = root / 'shard-000'
    m = json.loads((dr / 'manifest.json').read_text(encoding='utf-8'))
    d, cfg = m['design'], m['config']; validate(d)
    check(digest(sources()) == m['source_sha256'] == digest(m['source_bundle']), 'Source mismatch')
    check(all(cfg[k] == d[k] for k in ('temperature', 'top_p', 'max_tokens')), 'Config mismatch')
    check_server(cfg, json.loads((dr / 'server_metadata.json').read_text(encoding='utf-8')), False)
    check(len(list(dr.glob('result-*.json'))) == 12 and len(list(dr.glob('i*.json'))) == 144,
          'Incomplete/extra records; do not impute or apply gate')
    audit = AuditClient(dr, cfg); rows = []; grouped = {}; symmetry = {'Y': [], 'Z': []}; recoding = []
    for cell, (r, t) in enumerate(grid(d)):
        result = json.loads((dr / f'result-{cell}.json').read_text(encoding='utf-8'))
        check(result == run_cell(audit, d, cell), 'Result/order mismatch')
        a = r * t + (1-r) * (1-t)
        for value in (0, 1):
            for encoding in ('Y', 'Z'):
                shown = value if encoding == 'Y' else 1-value
                q = a if shown else 1-a
                for repeat in range(d['repetitions']):
                    v = result['outputs'][key_for(cell, value, encoding, repeat)]; p = v['p_state_1']
                    metrics = {'mse': (p-q)**2, 'mae': abs(p-q), 'direction': int(v['answer'] == shown),
                               'probability_direction': int(p != .5 and int(p > .5) == shown),
                               'inconsistent': int(p != .5 and v['answer'] != int(p > .5)),
                               'tie': int(p == .5)}
                    # Raw p/answer are never repaired. Complement only for the
                    # predeclared cross-encoding comparison, not output correction.
                    rows.append({'cell': cell, 'r': r, 't': t, 'original_value': value,
                                 'shown_value': shown, 'encoding': encoding, 'repeat': repeat,
                                 'oracle_p': q, 'raw_p': p, 'raw_answer': v['answer'], **metrics})
                    grouped.setdefault(f'{encoding}_shown{shown}', []).append(metrics)
        for repeat in range(d['repetitions']):
            for encoding in ('Y', 'Z'):
                ps = [result['outputs'][key_for(cell, v, encoding, repeat)]['p_state_1'] for v in (0, 1)]
                symmetry[encoding].append(abs(sum(ps)-1))
            for value in (0, 1):
                py = result['outputs'][key_for(cell, value, 'Y', repeat)]['p_state_1']
                pz = result['outputs'][key_for(cell, value, 'Z', repeat)]['p_state_1']
                recoding.append(abs(py-(1-pz)))
    metric_names = ('mse', 'mae', 'direction', 'probability_direction', 'inconsistent', 'tie')
    overall = {k: mean(row[k] for row in rows) for k in metric_names}
    strata = {s: {k: mean(row[k] for row in rs) for k in metric_names} for s, rs in grouped.items()}
    symmetry = {k: mean(v) for k, v in symmetry.items()}; recoding = mean(recoding)
    complete = all(isinstance(c['usage'], dict) and all(type(c['usage'].get(k)) is int and
                   c['usage'][k] >= 0 for k in ('prompt_tokens', 'completion_tokens')) for c in audit.calls)
    report = {'status': 'FINITE_GRID_COMPETENCE_HOLDOUT', 'cells': 12, 'calls': 144,
              'overall': overall, 'strata': strata, 'complement_symmetry': symmetry,
              'recoding_discrepancy': recoding, 'gate': gate(overall, strata, symmetry, recoding, d['criteria']),
              'usage_complete': complete,
              'prompt_tokens': sum(c['usage']['prompt_tokens'] for c in audit.calls) if complete else None,
              'completion_tokens': sum(c['usage']['completion_tokens'] for c in audit.calls) if complete else None,
              'sum_call_seconds': sum(c['wall_seconds'] for c in audit.calls),
              'warning': 'Fresh parameter grid, not independent tasks or population validation. Y/Z renaming plus label complement is a bundled representation check. Prior .5 and computed likelihood make this an easy single-observation screen. No communication or DICE efficacy tested.'}
    hashes = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(dr.rglob('*.json'))}
    return report, rows, hashes


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--run', required=True); p.add_argument('--audit-only', action='store_true')
    a = p.parse_args(); root = Path(a.run)
    if a.audit_only:
        print(json.dumps(diagnostics(root), indent=2))
    else:
        s, r, h = summarize(root)
        save(root / 'summary.json', s); save(root / 'cell_metrics.json', r); save(root / 'audit_input_hashes.json', h)
        print(json.dumps(s, indent=2))
