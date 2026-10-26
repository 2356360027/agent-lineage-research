"""Publish aggregate E3 records only after identical frozen rescoring."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_local_separation import sha,save


def export(first,second,record,readout):
    for name in ('summary.json','input_hashes.json'):
        if sha(first/name)!=sha(second/name):raise ValueError('Repeated frozen audit differs')
    d=json.loads((first/'summary.json').read_bytes())
    if d['status']!='E3_COMPLETED_FROZEN_SCORE_REPRODUCED' or not d['all_services_closed']:
        raise ValueError('Complete verified E3 required')
    public={k:v for k,v in d.items() if k not in ('rows','prepared')}
    public.update(prepared=str(Path(d['prepared']).relative_to(ROOT)).replace('\\','/'),
        summary_sha256=sha(first/'summary.json'),input_hashes_sha256=sha(first/'input_hashes.json'),
        source_rescoring_repeated=True)
    save(record,public)
    lines=[d['model_family']+' E3: actual cache transformations and measured fallback',
        '12 preselected bases/templates,24 positive/negative contexts,432 logical cells.',
        'Not432 independent tasks. Correct public SQL supplied; tiny controlled fixtures.',
        '', 'method price final_correct/24 cache_first_correct fallback no_answer reuse_selected']
    for method,prices in d['groups'].items():
        for price,r in prices.items():
            lines.append(f'{method} {price}: {r["final_correct"]}/24 {r["cache_first_correct"]} '
                f'{r["fallbacks"]} {r["no_final_answer"]} {r["reuse_selected"]}; first={r["first_statuses"]}')
    lines+=['', 'Costs (per-cell logical workflow; price1; no electricity measurement):']
    for method,prices in d['groups'].items():
        r=prices['1'];lines.append(method+': '+json.dumps({k:r[k] for k in (
            'logical_calls','logical_input_tokens','logical_output_tokens',
            'mean_component_latency_seconds','mean_modeled_total_seconds_by_cache_uses')}))
    lines+=['',d['cost_warning'],d['scope_warning'],
        'Invalid output and explicit refusal are distinct. Neither is an executed correct cache conversion.',
        'Final correctness recovered by public-Q fallback is not useful reuse or semantic validation.',
        'Physical new generation usage: '+json.dumps(d['physical_new_transform_usage']),
        'Cumulative goal attempts: '+str(d['goal_new_attempts_total']),
        'Cumulative owned-service seconds: '+str(d['goal_service_seconds_total']),
        'Summary SHA256: '+public['summary_sha256'],
        'Remaining work includes label controls, final synthesis, figures, reproduction and public release.']
    with readout.open('x',encoding='utf-8') as f:f.write('\n'.join(lines)+'\n')
    print(json.dumps({k:public[k] for k in ('status','model_family','summary_sha256','goal_new_attempts_total')},indent=2))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('first','second','record','readout'):p.add_argument('--'+key,type=Path,required=True)
    a=p.parse_args();export(a.first,a.second,a.record,a.readout)
