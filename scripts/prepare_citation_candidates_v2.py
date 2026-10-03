"""Private label-free candidate packet and separate gold; no inference."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.e1 import save, digest
from runtime.citation_interface_v2 import DATASET_SHA, PARENT_CANDIDATES_SHA, validate_design
from runtime.natural_qa_data import prediction_view, gold_view, seeded_order


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parquet', required=True)
    p.add_argument('--previous-candidates', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    import pyarrow.parquet as pq
    source, previous, out = Path(a.parquet), Path(a.previous_candidates), Path(a.output)
    if out.exists():
        raise ValueError('Output exists; no overwrite')
    if hashlib.sha256(source.read_bytes()).hexdigest() != DATASET_SHA:
        raise ValueError('Dataset checksum mismatch')
    if hashlib.sha256(previous.read_bytes()).hexdigest() != PARENT_CANDIDATES_SHA:
        raise ValueError('Previous candidate packet checksum mismatch')
    excluded = sorted(v['id'] for v in json.loads(previous.read_text())['views'])
    if len(excluded) != len(set(excluded)) or len(excluded) != 200:
        raise ValueError('Previous candidate count')
    d = json.loads(Path('configs/citation_interface_v2.json').read_text())
    validate_design(d)
    table = pq.read_table(source).to_pylist()
    if len(table) != 7405 or not set(excluded) <= {r['id'] for r in table}:
        raise ValueError('Full validation frame changed')
    native = [dict(_id=r['id'], question=r['question'], answer=r['answer'],
        context=list(zip(r['context']['title'], r['context']['sentences'], strict=True)),
        supporting_facts=list(zip(r['supporting_facts']['title'], r['supporting_facts']['sent_id'], strict=True)))
        for r in table if r['id'] not in set(excluded)]
    ordered = seeded_order(native, d['seed'])
    selected = ordered[:64]
    views = [prediction_view(r) for r in selected]
    gold = {r['_id']: gold_view(r, v) for r, v in zip(selected, views, strict=True)}
    md = dict(dataset='HotpotQA', split='distractor validation', file_sha256=DATASET_SHA,
        mirror_revision='1908d6afbbead072334abe2965f91bd2709910ab', license='CC-BY-SA-4.0',
        attribution='Yang et al. 2018. HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering. EMNLP.',
        preparation_unix=time.time(), preparation_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        parent_candidates_file_sha256=PARENT_CANDIDATES_SHA, excluded_ids=excluded,
        all_ordered_ids=[r['_id'] for r in ordered], design_sha256=digest(d), scoring_gold_sha256=digest(gold))
    out.mkdir(parents=True)
    save(out/'candidates.json', dict(metadata=md, views=views))
    save(out/'gold.json', dict(dataset_sha256=DATASET_SHA, gold=gold))
    save(out/'provenance.json', dict(metadata=md, candidate_sha256=digest(views)))
    print(json.dumps(dict(kind='PREPARED_NO_INFERENCE', candidates=64, excluded=len(excluded), output=str(out))))


if __name__ == '__main__':
    main()
