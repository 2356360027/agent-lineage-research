"""Two fixed public-text API checks, NOT a research experiment.

Default is offline plan-only. --execute requires a key via hidden terminal input
or DEEPSEEK_API_KEY. Keys are never command arguments, saved, or printed. No
questionnaires, databases, review labels, or historical logs are loaded.
"""
import argparse
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.error
import urllib.request
import warnings

URL = 'https://api.deepseek.com/v1/chat/completions'
MODEL = 'deepseek-flash'
PROMPTS = (
    'Return a JSON object with exactly one field: "status", with value "ok".',
    'Return a JSON object with exactly these fields: "operation": "count", "unit": "distinct_items".',
)
EXPECTED = ({'status': 'ok'}, {'operation': 'count', 'unit': 'distinct_items'})


def payload(index):
    return {'model': MODEL, 'thinking': {'type': 'disabled'}, 'temperature': 0,
            'max_tokens': 128, 'stream': False, 'response_format': {'type': 'json_object'},
            'messages': [{'role': 'system', 'content': 'Follow the requested JSON format.'},
                         {'role': 'user', 'content': PROMPTS[index]}]}


def plan():
    return {'purpose': 'PUBLIC_SYNTHETIC_INTERFACE_CHECK_NOT_RESEARCH', 'endpoint': URL,
            'requested_model': MODEL, 'maximum_calls': 2, 'max_output_tokens_per_call': 128,
            'worker_deadline_seconds_per_call': 90, 'automatic_retry': False,
            'thinking': 'disabled', 'credentials_saved': False, 'private_inputs_sent': False,
            'expected_cost_cny_less_than': 0.01, 'estimate_not_provider_spending_limit': True,
            'peak_price_cny_per_million_input_miss': 2,
            'peak_price_cny_per_million_output': 8,
            'price_checked_date': '2026-10-24',
            'price_source': 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/',
            'request_payloads': [payload(i) for i in range(2)]}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)


def worker():
    """Parent passes secret in an anonymous pipe; no key in argv/files/logs."""
    packet = json.loads(sys.stdin.buffer.read(8192))
    key = packet['key']
    if (type(key) is not str or not key or len(key) > 1024 or '\n' in key or '\r' in key
            or packet['index'] not in (0, 1)):
        raise ValueError('Invalid worker input')
    raw_payload = json.dumps(payload(packet['index'])).encode('utf-8')
    request = urllib.request.Request(URL, data=raw_payload, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    # No ambient proxies, redirects, SDK retries, alternate endpoints or TLS bypass.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=30) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            result = {'transport_status': 'RESPONSE_TOO_LARGE', 'billing_unknown': True}
        else:
            # Defensive redaction if a server were ever to echo the credential.
            text = raw.decode('utf-8').replace(key, '[REDACTED]')
            result = {'transport_status': 'RESPONSE_RECEIVED', 'response_text': text}
    except urllib.error.HTTPError as exc:
        # Error bodies / request headers are intentionally not logged.
        result = {'transport_status': 'HTTP_ERROR', 'http_status': exc.code, 'billing_unknown': True}
    except Exception as exc:
        result = {'transport_status': 'TRANSPORT_ERROR', 'error_type': type(exc).__name__,
                  'billing_unknown': True}
    sys.stdout.write(json.dumps(result))


def invoke(index, key):
    command = [sys.executable, str(Path(__file__).resolve()), '--worker']
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        completed = subprocess.run(command, input=json.dumps({'index': index, 'key': key}).encode(),
            capture_output=True, timeout=90, creationflags=flags)
    except subprocess.TimeoutExpired:
        # subprocess.run kills and waits for the owned worker. Do not repeat this call.
        return {'transport_status': 'WORKER_TIMEOUT', 'billing_unknown': True}
    if completed.returncode:
        return {'transport_status': 'WORKER_FAILED', 'returncode': completed.returncode,
                'billing_unknown': True}
    # Never print stderr or exception repr: either could contain unsafe provider data.
    return json.loads(completed.stdout.decode().replace(key, '[REDACTED]'))


def assess(record, index):
    if record.get('transport_status') != 'RESPONSE_RECEIVED':
        return {'status': 'STOP_TRANSPORT_UNCERTAIN', 'billing_unknown': True}
    try:
        raw = json.loads(record['response_text'])
        usage = raw['usage']
        counts = [usage['prompt_tokens'], usage['completion_tokens']]
        if any(type(n) is not int or n < 0 for n in counts):
            raise ValueError('Invalid usage')
        if counts[0] > 2048 or counts[1] > 128:
            raise ValueError('Unexpected usage; stop')
        choices = raw['choices']
        if len(choices) != 1 or choices[0]['finish_reason'] != 'stop':
            raise ValueError('Invalid completion')
        message = choices[0]['message']
        if message.get('reasoning_content') or message.get('tool_calls'):
            raise ValueError('Unexpected thinking or tools')
        if json.loads(message['content']) != EXPECTED[index]:
            raise ValueError('Output did not match the fixed interface test')
        if not isinstance(raw.get('model'), str) or not raw['model']:
            raise ValueError('Missing served model identity')
        return {'status': 'INTERFACE_CHECK_PASSED', 'reported_model': raw['model'],
                'reported_usage': usage,
                'peak_uncached_cost_estimate_cny': (counts[0]*2 + counts[1]*8)/1_000_000,
                'provider_invoice_verified': False, 'research_samples': 0}
    except (KeyError, ValueError, TypeError, IndexError):
        return {'status': 'STOP_INVALID_RESPONSE_OR_USAGE', 'billing_unknown': True}


def save_new(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)


def execute(key, output, transport=invoke):
    output.mkdir(parents=True, exist_ok=False)
    save_new(output/'plan.json', plan())
    source = Path(__file__).read_bytes()
    (output/'probe_source.py').write_bytes(source)
    save_new(output/'source_identity.json', {'sha256': hashlib.sha256(source).hexdigest()})
    rows = []
    for index in range(2):
        save_new(output/f'call-{index+1}.pending.json', {'index': index, 'automatic_retry': False})
        start = time.monotonic()
        record = transport(index, key)
        # Last-line redaction also protects saved fake/adversarial responses in tests.
        record = json.loads(json.dumps(record).replace(key, '[REDACTED]'))
        save_new(output/f'call-{index+1}.response.json', record)
        row = assess(record, index) | {'call': index+1, 'wall_seconds': time.monotonic()-start}
        save_new(output/f'call-{index+1}.assessment.json', row)
        rows.append(row)
        if row['status'] != 'INTERFACE_CHECK_PASSED':
            break
    summary = {'status': 'PROBE_FINISHED' if len(rows)==2 and all(
        r['status']=='INTERFACE_CHECK_PASSED' for r in rows) else 'PROBE_STOPPED_NO_RETRY',
        'attempted_calls': len(rows), 'planned_calls': 2, 'rows': rows, 'research_samples': 0,
        'utc_time': datetime.now(timezone.utc).isoformat(), 'private_inputs_sent': False}
    save_new(output/'summary.json', summary)
    save_new(output/'artifact_hashes.json', {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(output.iterdir()) if p.is_file()})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker()
        return
    if not args.execute:
        print(json.dumps(plan(), indent=2))
        return
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key:
        # Abort rather than fall back to visible input on non-interactive terminals.
        with warnings.catch_warnings():
            warnings.simplefilter('error', getpass.GetPassWarning)
            key = getpass.getpass('New DeepSeek API key (hidden, not saved): ')
    if not key or not key.isascii() or any(c.isspace() for c in key) or len(key)>1024:
        raise ValueError('Invalid key input')
    root = Path(__file__).resolve().parents[1]
    output = root/'runtime'/'runs'/('deepseek-official-probe-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    result = execute(key, output)
    print(json.dumps({'status': result['status'], 'attempted_calls': result['attempted_calls'],
                      'output': str(output), 'research_samples': 0}, indent=2))


if __name__ == '__main__':
    main()
