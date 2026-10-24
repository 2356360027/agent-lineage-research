"""Offline migration audit and private prompt proposal; NEVER calls an API.

This is not a runnable research batch. It preserves the paired task packets but
explicitly moves schema instructions into the prompt for JSON-object-only APIs.
It must not be passed to the Qwen-specific capacity or rollout implementation.
"""
import argparse
from copy import deepcopy
from pathlib import Path
import json

from runtime import cache_intent_entry as entry
from runtime import cache_intent_prompts as prompts
from runtime import bird_readonly_audit as audit


def proposed_contract_payload(job):
    messages = deepcopy(job['envelope']['messages'])
    schema_instruction = ('\nProvider adaptation for this separate batch: return one JSON object '
        'with exactly the following eight keys; every value must be a string. '
        'Field descriptions follow (they are not task answers):\n' +
        json.dumps(prompts._DESCRIPTIONS, ensure_ascii=False, sort_keys=True))
    messages[0]['content'] += schema_instruction
    return {'model': 'deepseek-flash', 'messages': messages,
            'thinking': {'type': 'disabled'}, 'temperature': 0, 'max_tokens': 512,
            'stream': False, 'response_format': {'type': 'json_object'}}


def proposal(plan):
    records = [{'id': job['id'], 'history_id': job['history_id'],
                'declared_cache_read_price': job['read_price'],
                'legacy_seed_not_sent': job['seed'],
                'original_messages_sha256': audit.digest(job['envelope']['messages']),
                'proposed_payload': proposed_contract_payload(job)} for job in plan['jobs']]
    # Same new schema instruction in both arms; packet content differs by price only.
    pair_audits = []
    for history in sorted({r['history_id'] for r in records}):
        pair = [r for r in records if r['history_id']==history]
        if len(pair) != 2 or {r['declared_cache_read_price'] for r in pair} != {1,8}:
            raise ValueError('Fixed paired allocation missing')
        normalized = []
        for record in pair:
            p = deepcopy(record['proposed_payload'])
            packet = json.loads(p['messages'][1]['content'])
            if packet['context']['cache']['read_cost'] != record['declared_cache_read_price']:
                raise ValueError('Price metadata mismatch')
            packet['context']['cache']['read_cost'] = 'PAIR_PRICE_REMOVED_FOR_AUDIT_ONLY'
            p['messages'][1]['content'] = prompts._json(packet)
            normalized.append(p)
        if normalized[0] != normalized[1]:
            raise ValueError('More than cache read price differs inside pair')
        pair_audits.append({'history_id': history, 'only_within_pair_difference': 'context.cache.read_cost'})
    return records, pair_audits


def run(inputs, output):
    plan, _, _ = entry.load_private_inputs(inputs, entry.FIXED_INPUT_SHA, entry.FIXED_PLAN_SHA)
    records, pairs = proposal(plan)
    review = {
        'status': 'OFFLINE_MIGRATION_AUDIT_NOT_LIVE_ADMISSION',
        'original_input_manifest_sha256': entry.FIXED_INPUT_SHA,
        'original_plan_sha256': entry.FIXED_PLAN_SHA,
        'proposed_initial_payload_count': len(records), 'independent_targets': 2,
        'pair_audits': pairs, 'private_payloads_sha256': audit.digest(records),
        'initial_message_utf8_bytes': [sum(len(m['content'].encode('utf-8'))
            for m in r['proposed_payload']['messages']) for r in records],
        'byte_counts_are_not_token_counts': True,
        'calls_sent': 0, 'sql_queries': 0, 'semantic_judgments': 0,
        'inference_authorized': False, 'live_ready': False,
        'adaptation': {
            'schema_mode': 'JSON object plus explicit common field instructions, no strict decoding',
            'strict_schema_equivalence_claimed': False,
            'seed': 'Legacy labels retained for traceability only; not sent to API',
            'sampling': 'temperature 0, thinking disabled; not a reproducibility guarantee',
            'original_task_packet_changed': False,
            'compare_with_old_qwen_as_same_condition': False,
            'tool_result_exposure_policy': 'UNRESOLVED; Qwen tokenization cannot certify DeepSeek exposure',
            'full_context_capacity': 'UNRESOLVED; do not reuse Qwen framing or window proof',
            'followup_program_required_for_type_C': True,
            'initial_contract_only_is_not_completion_of_the_research_batch': True
        },
        'conditional_cost_scenario_not_verified_bound': {
            'assumed_calls': 276, 'assumed_max_input_tokens_per_call': 32256,
            'assumed_max_output_tokens_per_call': 512,
            'offpeak_uncached_cny': 276*(32256+512*4)/1_000_000,
            'peak_uncached_cny': 276*(32256*2+512*8)/1_000_000,
            'requires_validated_token_bounds_and_current_prices': True,
            'provider_spending_limit_installed': False,
            'displayed_account_balance_is_not_full_batch_approval': True
        },
        'remaining_gates': [
            'User-run two-call official probe and review of its raw usage/model/finish records',
            'Model-matched tokenizer/template or justified provider-specific capacity procedure',
            'Explicit full-or-withheld observation rule with pinned counters, same within paired arms',
            'Credentials kept outside source/history/logs; egress package excludes human-review material',
            'Freeze full trajectory adapter, accounting, failure handling, and time ceiling',
            'Obtain fixed paid-batch cost ceiling approval before research calls',
            'Independent whole-contract and later-program review; retain missing and unfavorable cases'
        ],
        'sources': [
            'https://api-docs.deepseek.com/api/create-chat-completion/',
            'https://api-docs.deepseek.com/quick_start/token_usage/',
            'https://api-docs.deepseek.com/guides/json_mode/',
            'https://github.com/deepseek-ai/deepseek-recipe/blob/main/docs/tokenizer.md'
        ]
    }
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    audit.save_new(output/'PRIVATE_proposed_initial_payloads.json', records)
    audit.save_new(output/'audit.json', review)
    (output/'audit_source.py').write_bytes(Path(__file__).read_bytes())
    audit.save_new(output/'artifact_hashes.json', {p.name:audit.hash_file(p)
        for p in sorted(output.iterdir()) if p.is_file()})
    return review


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--inputs', required=True)
    p.add_argument('--output', required=True)
    args=p.parse_args()
    print(json.dumps(run(args.inputs,args.output),indent=2))


if __name__=='__main__': main()
