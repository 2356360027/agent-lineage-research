"""Convert a pinned HotpotQA mirror to separate label-free inputs and scoring gold.

No inference. Outputs are private local artifacts, not synthetic measurements.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runtime.e1 import save, digest
from runtime.natural_qa_data import prediction_view, gold_view, seeded_order

REVISION = '1908d6afbbead072334abe2965f91bd2709910ab'
FILE_SHA = 'c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6'
URL = ('https://huggingface.co/datasets/hotpotqa/hotpot_qa/resolve/' + REVISION +
       '/distractor/validation-00000-of-00001.parquet')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parquet', required=True)
    p.add_argument('--output', required=True)
    a = p.parse_args()
    import pyarrow.parquet as pq
    source = Path(a.parquet)
    if hashlib.sha256(source.read_bytes()).hexdigest() != FILE_SHA:
        raise ValueError('Pinned dataset file checksum mismatch')
    out = Path(a.output)
    if out.exists():
        raise ValueError('Output exists; no overwrite')
    table = pq.read_table(source).to_pylist()
    if len(table) != 7405:
        raise ValueError('Expected full distractor validation split')
    native = []
    for r in table:
        native.append(dict(_id=r['id'], question=r['question'], answer=r['answer'],
            context=list(zip(r['context']['title'], r['context']['sentences'], strict=True)),
            supporting_facts=list(zip(r['supporting_facts']['title'],
                                      r['supporting_facts']['sent_id'], strict=True))))
    design = json.loads(Path('configs/natural_repetition_v1.json').read_text())
    ordered = seeded_order(native, design['seed'])
    candidates = ordered[:design['candidate_items']]
    views = [prediction_view(r) for r in candidates]
    gold = {r['_id']: gold_view(r, v) for r, v in zip(candidates, views, strict=True)}
    metadata = dict(dataset='HotpotQA', split='distractor validation',
        homepage='https://hotpotqa.github.io/', download_url=URL,
        mirror_revision=REVISION, file_sha256=FILE_SHA, license='CC-BY-SA-4.0',
        attribution='Yang et al. 2018. HotpotQA: A Dataset for Diverse, Explainable Multi-hop Question Answering. EMNLP.',
        source_rows=len(table), preparation_unix=time.time(),
        preparation_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        official_server_download='Attempted HTTPS download from curtis.ml.cmu.edu; connection timed out. Uses pinned Hugging Face mirror, not verified byte-identical original JSON.',
        design_sha256=digest(design), scoring_gold_sha256=digest(gold),
        all_ordered_ids=[r['_id'] for r in ordered])
    out.mkdir(parents=True)
    save(out/'candidates.json', dict(metadata=metadata, views=views))
    save(out/'gold.json', dict(dataset_sha256=FILE_SHA, gold=gold))
    save(out/'provenance.json', dict(metadata=metadata,
        candidate_sha256=digest(views), gold_sha256=digest(gold)))
    print(json.dumps(dict(candidate_items=len(views), candidate_sha256=digest(views),
                          dataset_sha256=FILE_SHA, output=str(out)), indent=2))


if __name__ == '__main__':
    main()
