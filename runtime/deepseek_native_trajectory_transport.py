"""Explicit multi-turn native transport; not an experiment authorization.

Keep the historical first-response transport unchanged. Only the source-bound
symmetric native history is accepted here. Credentials travel through owned
worker stdin, never argv, journal records, environment exports or disk. A future
frozen launcher must provide approval, accounting and an overall hard deadline.
"""
import json
import sys
import urllib.error
import urllib.request

from runtime.deepseek_official_transport import (
    MAX_PACKET, MAX_RESPONSE, NoRedirect, redact, run_owned, validate_key)
from runtime.deepseek_native_transport import validate_timeout
from runtime.native_action_interface import ENDPOINT
from runtime.native_trajectory_counter import validate_trajectory_payload

PROFILE = 'native-symmetric-trajectory-transport-v1'
MODULE = 'runtime.deepseek_native_trajectory_transport'


def encode_payload(payload):
    validate_trajectory_payload(payload)
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_PACKET - 4096:
        raise ValueError('Native trajectory payload too large; never truncate')
    return raw


def worker_request(packet):
    if (type(packet) is not dict
            or set(packet) != {'profile', 'key', 'payload', 'timeout'}
            or packet['profile'] != PROFILE):
        raise ValueError('Explicit trajectory transport profile required')
    validate_key(packet['key'])
    validate_timeout(packet['timeout'])
    key = packet['key']
    data = encode_payload(packet['payload'])
    request = urllib.request.Request(ENDPOINT, data=data, method='POST',
        headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=min(30, packet['timeout'])) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return {'transport_status': 'RESPONSE_TOO_LARGE', 'billing_unknown': True}
        return {'transport_status': 'RESPONSE_RECEIVED',
                'response_text': redact(raw.decode('utf-8'), key)}
    except urllib.error.HTTPError as exc:
        return {'transport_status': 'HTTP_ERROR', 'http_status': exc.code,
                'billing_unknown': True}
    except Exception as exc:
        return {'transport_status': 'TRANSPORT_ERROR',
                'error_type': type(exc).__name__, 'billing_unknown': True}


def invoke(payload, key, timeout):
    validate_key(key)
    validate_timeout(timeout)
    encode_payload(payload)
    packet = json.dumps(dict(profile=PROFILE, payload=payload, key=key, timeout=timeout),
                        ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(packet) > MAX_PACKET:
        raise ValueError('Worker packet too large')
    result = run_owned([sys.executable, '-B', '-m', MODULE, '--worker'], packet, timeout)
    return json.loads(redact(json.dumps(result), key))


class NativeTrajectoryTransport:
    """Single trajectory, at most four attempts, no automatic retry or resume.

The accounting adapter owns continuity and token/cost guards. The transport
independently validates each history and refuses an implicit fifth attempt.
"""
    def __init__(self, key, save):
        validate_key(key)
        self._key, self.save = key, save
        self.count, self.failed = 0, False

    def __call__(self, payload, timeout):
        if self.failed or self.count >= 4:
            raise RuntimeError('Terminal trajectory transport; no retry')
        try:
            self.count += 1
            record = invoke(payload, self._key, timeout)
            self.save(f'transport-{self.count:03d}', record)
            if record.get('transport_status') != 'RESPONSE_RECEIVED':
                raise RuntimeError('Native trajectory request failed; retain billing reserve')
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


if __name__ == '__main__':
    main()
