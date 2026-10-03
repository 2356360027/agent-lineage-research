"""Private label-free fresh candidate packet and separately stored scoring gold."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.e1 import save, digest
from runtime.natural_repetition_v2 import DATASET_SHA, NATURAL_CANDIDATES_SHA, CITATION_CANDIDATES_SHA, validate_design
from runtime.natural_qa_data import prediction_view, gold_view, seeded_order


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parquet', required=True)
    p.add_argument('--natural-candidates', required=True)
    p.add_argument('--citation-candidates', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    import pyarrow.parquet as pq
    source, previous, citation, out = map(Path, (a.parquet, a.natural_candidates, a.citation_candidates, a.output))
    if out.exists():
        raise ValueError('Output exists; no overwrite')
    for path, expected in ((source, DATASET_SHA), (previous, NATURAL_CANDIDATES_SHA), (citation, CITATION_CANDIDATES_SHA)):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Input checksum mismatch: ' + str(path))
    old_ids = [v['id'] for v in json.loads(previous.read_text())['views']]
    citation_ids = [v['id'] for v in json.loads(citation.read_text())['views']]
    excluded = sorted(set(old_ids) | set(citation_ids))
    if len(old_ids) != len(set(old_ids)) or len(old_ids) != 200 or len(citation_ids) != len(set(citation_ids)) or len(citation_ids) != 64 or len(excluded) != 264:
        raise ValueError('Exclusion frame changed')
    d = json.loads((Path(__file__).resolve().parents[1] / 'configs/natural_repetition_v2.json').read_text())
    validate_design(d)
    table = pq.read_table(source).to_pylist()
    if len(table) != 7405 or len({r['id'] for r in table}) != 7405 or not set(excluded) <= {r['id'] for r in table}:
        raise ValueError('Validation frame changed')
    native = [dict(_id=r['id'], question=r['question'], answer=r['answer'],
        context=list(zip(r['context']['title'], r['context']['sentences'], strict=True)),
        supporting_facts=list(zip(r['supporting_facts']['title'], r['supporting_facts']['sent_id'], strict=True)))
        for r in table if r['id'] not in set(excluded)]
    ordered = seeded_order(native, d['seed'])
    selected = ordered[:200]
    views = [prediction_view(r) for r in selected]
    gold = {r['_id']: gold_view(r, v) for r, v in zip(selected, views, strict=True)}
    md = dict(dataset='HotpotQA', split='distractor validation', file_sha256=DATASET_SHA,
        mirror_revision='1908d6afbbead072334abe2965f91bd2709910ab', license='CC-BY-SA-4.0',
        attribution='Yang et al. 2018. HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering. EMNLP.',
        preparation_unix=time.time(), preparation_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        natural_candidates_file_sha256=NATURAL_CANDIDATES_SHA, citation_candidates_file_sha256=CITATION_CANDIDATES_SHA,
        excluded_ids=excluded, all_ordered_ids=[r['_id'] for r in ordered], design_sha256=digest(d), scoring_gold_sha256=digest(gold))
    out.mkdir(parents=True)
    save(out / 'candidates.json', dict(metadata=md, views=views))
    save(out / 'gold.json', dict(dataset_sha256=DATASET_SHA, gold=gold))
    save(out / 'provenance.json', dict(metadata=md, candidate_sha256=digest(views)))
    print(json.dumps(dict(kind='PREPARED_NO_INFERENCE', candidates=200, excluded=len(excluded), output=str(out))))


if __name__ == '__main__':
    main()
