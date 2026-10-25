"""Separate official beta transport; no experiment launcher or retry.

Caller must supply a frozen approved batch. The legacy text-only transport and
its payload validator stay unchanged. Credentials enter only through stdin.
"""
import json
import math
from pathlib import Path
import sys
import urllib.error
import urllib.request

from runtime.deepseek_official_transport import (
    MAX_PACKET, MAX_RESPONSE, NoRedirect, redact, run_owned, validate_key)
from runtime.native_action_counter import validate_candidate_payload
from runtime.native_action_interface import ENDPOINT


def validate_timeout(timeout):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 90:
        raise ValueError('Timeout must be within 90 seconds')


def encode_payload(payload):
    validate_candidate_payload(payload)
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_PACKET - 4096:
        raise ValueError('Native payload too large; never truncate')
    return raw


def worker_request(packet):
    if type(packet) is not dict or set(packet) != {'key', 'payload', 'timeout'}:
        raise ValueError('Unexpected worker packet')
    validate_key(packet['key']); validate_timeout(packet['timeout'])
    key = packet['key']
    data = encode_payload(packet['payload'])
    req = urllib.request.Request(ENDPOINT, data=data, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(req, timeout=min(30, packet['timeout'])) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return {'transport_status': 'RESPONSE_TOO_LARGE', 'billing_unknown': True}
        return {'transport_status': 'RESPONSE_RECEIVED',
                'response_text': redact(raw.decode('utf-8'), key)}
    except urllib.error.HTTPError as exc:
        return {'transport_status': 'HTTP_ERROR', 'http_status': exc.code, 'billing_unknown': True}
    except Exception as exc:
        return {'transport_status': 'TRANSPORT_ERROR', 'error_type': type(exc).__name__,
                'billing_unknown': True}


def invoke(payload, key, timeout):
    validate_key(key); validate_timeout(timeout); encode_payload(payload)
    packet = json.dumps({'payload': payload, 'key': key, 'timeout': timeout},
                        ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(packet) > MAX_PACKET:
        raise ValueError('Worker packet too large')
    # Caller must use its frozen source as cwd, as checked by a future launcher.
    result = run_owned([sys.executable, '-B', '-m', 'runtime.deepseek_native_transport', '--worker'],
                       packet, timeout)
    return json.loads(redact(json.dumps(result), key))


class NativeTransport:
    """Preserve complete response text before any JSON parsing or scoring."""
    def __init__(self, key, save):
        validate_key(key)
        self._key, self.save = key, save
        self.count, self.failed = 0, False

    def __call__(self, payload, timeout):
        if self.failed:
            raise RuntimeError('Terminal transport; do not retry')
        try:
            self.count += 1
            record = invoke(payload, self._key, timeout)
            self.save(f'transport-{self.count:03d}', record)
            if record.get('transport_status') != 'RESPONSE_RECEIVED':
                raise RuntimeError('Native request failed; retain billing reserve')
            if type(record.get('response_text')) is not str:
                raise ValueError('Missing raw response text')
            return record['response_text']
        except BaseException:
            self.failed = True
            raise


def main():
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('No standalone experiment command; approved frozen launcher required')
    raw = sys.stdin.buffer.read(MAX_PACKET + 1)
    try:
        if len(raw) > MAX_PACKET:
            raise ValueError('Packet too large')
        result = worker_request(json.loads(raw))
    except Exception as exc:
        result = {'transport_status': 'WORKER_VALIDATION_FAILED',
                  'error_type': type(exc).__name__, 'billing_unknown': False,
                  'request_dispatched': False}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__': main()
