"""Finite-domain cache collision experiment, no model or external database.

A witness proves insufficiency in this stated domain. Absence of a witness does
not establish unrestricted query equivalence. Classical transforms are controls,
not a proposed novel algorithm. All rows are researcher-generated.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import itertools
import json
from pathlib import Path
import time


def worlds(alphabet):
    for n in range(4):
        yield from itertools.product(alphabet, repeat=n)


def average(scores):
    values = [s for s in scores if s is not None]
    return None if not values else str(Fraction(sum(values), len(values)))


def groups(values):
    return tuple(sorted(Counter(values).items()))


def names(values):
    return tuple(sorted(set(values), key=lambda v:(v is not None, str(v))))


def top_group(values):
    ranked = sorted(groups(values), key=lambda pair:(-pair[1], pair[0]))
    return tuple(ranked[:1])


def avg_transform(cache):
    total, n = cache
    return None if n == 0 else str(Fraction(total, n))


def definitions():
    return [
        ('distinct_names_to_entity_count', ('A','B',None), names, len, None),
        ('count_rows_to_avg', (None,0,1,2), len, average, None),
        ('sum_and_nonnull_count_to_avg', (None,0,1,2),
         lambda v:(sum(x for x in v if x is not None),sum(x is not None for x in v)),
         average, avg_transform),
        ('complete_group_counts_to_gold_count', ('Gold','Blue','Red'), groups,
         lambda v:v.count('Gold'), lambda c:dict(c).get('Gold',0)),
        ('top_one_group_to_gold_count', ('Gold','Blue','Red'), top_group,
         lambda v:v.count('Gold'), None),
    ]


def analyse():
    summaries, witnesses, observations = [], {}, []
    for name, alphabet, view, target, transform in definitions():
        seen = {}
        collisions = 0
        transform_errors = 0
        count = 0
        for world in worlds(alphabet):
            count += 1
            cache, expected = view(world), target(world)
            observations.append(dict(family=name,world=world,cache=cache,target=expected))
            key = json.dumps(cache,sort_keys=True)
            if key in seen and seen[key]['target'] != expected:
                collisions += 1
                witnesses.setdefault(name, dict(first=seen[key],
                    second=dict(world=world,cache=cache,target=expected)))
            else:
                seen.setdefault(key,dict(world=world,cache=cache,target=expected))
            if transform is not None and transform(cache) != expected:
                transform_errors += 1
        summaries.append(dict(family=name, worlds=count, distinct_cache_values=len(seen),
            conflicting_worlds_against_first_in_bucket=collisions,
            insufficiency_witness_found=name in witnesses,
            classical_transform_tested=transform is not None,
            classical_transform_mismatches=transform_errors if transform is not None else None))
    return summaries, witnesses, observations


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path)
    a=p.parse_args()
    a.output.mkdir(parents=True,exist_ok=False)
    def save(name,value):
        with (a.output/name).open('x',encoding='utf-8') as f:
            json.dump(value,f,ensure_ascii=False,indent=2)
    source=Path(__file__).read_bytes()
    (a.output/'source.py').write_bytes(source)
    save('plan.json',dict(kind='FINITE_SYNTHETIC_CACHE_SUFFICIENCY',max_rows=3,
        families=[d[0] for d in definitions()],created_at=datetime.now(timezone.utc).isoformat(),
        rules='Row order encodes distinct entity IDs; equal names do not identify an entity. '
              'NULL score excluded from AVG. Top-one ties broken lexicographically.',
        source_sha256=hashlib.sha256(source).hexdigest(),no_real_data=True,model_calls=0))
    start=time.perf_counter()
    summaries,witnesses,observations=analyse()
    save('observations.json',observations)
    save('witnesses.json',witnesses)
    summary=dict(status='COMPLETED_FINITE_SYNTHETIC_CACHE_STUDY',families=summaries,
        elapsed_seconds=time.perf_counter()-start,model_calls=0,api_cost_cny=0,
        novel_method_claim=False,independent_real_tasks=0,
        conclusion='Three lossy views admit identical-cache/different-target witnesses; '
                   'two sufficient views have classical transformations. '
                   'Use actual per-family observations to verify; finite noncollision is not a proof.')
    save('summary.json',summary)
    save('sha256.json',{f.name:hashlib.sha256(f.read_bytes()).hexdigest()
                        for f in a.output.iterdir() if f.is_file()})
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
