"""Bounded read-only snapshot check, not human review or a model experiment."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import time

EXPECTED = "35ef27ae6bdfda530e125ed369666ac0abb8bc8c0bcc0ad09407f547ecb61a93"
QUERIES = {
    "whole_connection_counts": "SELECT COUNT(*) AS rows, COUNT(bond_id) AS nonnull_bond_rows, COUNT(DISTINCT bond_id) AS distinct_bonds FROM connected",
    "target_connection_counts": "SELECT COUNT(*) AS rows, COUNT(bond_id) AS nonnull_bond_rows, COUNT(DISTINCT bond_id) AS distinct_bonds FROM connected WHERE atom_id='TR009_12' OR atom_id2='TR009_12'",
    "target_with_molecule_filter": "SELECT COUNT(*) AS rows, COUNT(DISTINCT c.bond_id) AS distinct_bonds FROM connected c JOIN bond b ON b.bond_id=c.bond_id WHERE b.molecule_id='TR009' AND (c.atom_id='TR009_12' OR c.atom_id2='TR009_12')",
    "target_mismatched_or_missing_bond": "SELECT COUNT(*) AS rows FROM connected c LEFT JOIN bond b ON b.bond_id=c.bond_id WHERE (c.atom_id='TR009_12' OR c.atom_id2='TR009_12') AND (b.bond_id IS NULL OR b.molecule_id IS NULL OR b.molecule_id<>'TR009')",
    "whole_reverse_pairs": "SELECT COUNT(*) AS reverse_pairs FROM connected c JOIN connected d ON c.atom_id=d.atom_id2 AND c.atom_id2=d.atom_id AND c.bond_id=d.bond_id WHERE c.atom_id<c.atom_id2",
    "target_rows": "SELECT c.atom_id,c.atom_id2,c.bond_id,b.molecule_id FROM connected c LEFT JOIN bond b ON c.bond_id=b.bond_id WHERE c.atom_id='TR009_12' OR c.atom_id2='TR009_12' ORDER BY c.atom_id,c.atom_id2 LIMIT 101",
}


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def check(database, output):
    database, output = Path(database).resolve(strict=True), Path(output).absolute()
    if output.exists():
        raise FileExistsError('Preserve existing report')
    if any(Path(str(database)+s).exists() for s in ('-wal', '-shm', '-journal')):
        raise ValueError('Snapshot has sidecar files')
    before = digest(database)
    if before != EXPECTED:
        raise ValueError('Wrong frozen database')
    results = {}
    connection = sqlite3.connect(database.as_uri()+'?mode=ro&immutable=1', uri=True)
    try:
        connection.execute('PRAGMA query_only=ON')
        for name, sql in QUERIES.items():
            start = time.monotonic()
            connection.set_progress_handler(lambda: int(time.monotonic()-start > 5), 1000)
            try:
                cursor = connection.execute(sql)
                rows = cursor.fetchmany(102)
                results[name] = {'status':'EXECUTED', 'sql':sql,
                    'columns':[d[0] for d in cursor.description], 'rows':rows,
                    'truncated': name == 'target_rows' and len(rows)>100}
            except sqlite3.Error as exc:
                results[name] = {'status':'FAILED_PRESERVED','sql':sql,'error':str(exc)}
            results[name]['seconds'] = time.monotonic()-start
    finally:
        connection.close()
    after = digest(database)
    report = {'status':'SNAPSHOT_ONLY_NO_SEMANTIC_APPROVAL', 'database_sha256_before':before,
        'database_sha256_after':after, 'database_unchanged':before==after,
        'source_sha256':digest(Path(__file__)), 'sqlite_version':sqlite3.sqlite_version,
        'new_model_calls':0, 'human_labels_generated':False, 'results':results,
        'limitations':['Snapshot agreement is not equivalence on all legal databases.',
                       'No participant response is amended or automatically approved.']}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('x',encoding='utf-8') as stream:
        json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if before != after:
        raise RuntimeError('Database changed during read-only check')


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    check(args.database,args.output)
