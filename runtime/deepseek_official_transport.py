"""Bounded official-only transport; no launcher, retries, or stored credentials.

The caller must obtain fixed-batch authorization before using this transport.
Secrets enter the owned subprocess only through stdin. A client timeout does
not prove cancellation at the provider; retain the uncertain billing reserve.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import urllib.error
import urllib.request

URL = 'https://api.deepseek.com/v1/chat/completions'
MAX_PACKET = 4 * 1024 * 1024
MAX_RESPONSE = 1024 * 1024


def validate_payload(payload):
    fields = {'model','messages','thinking','temperature','max_tokens','stream','response_format'}
    if (type(payload) is not dict or set(payload) != fields
            or payload['model'] != 'deepseek-flash'
            or payload['thinking'] != {'type':'disabled'}
            or payload['temperature'] != 0 or payload['max_tokens'] != 512
            or payload['stream'] is not False
            or payload['response_format'] != {'type':'json_object'}):
        raise ValueError('Only fixed research payload settings are supported')
    messages = payload['messages']
    if type(messages) is not list or not 2 <= len(messages) <= 48:
        raise ValueError('Message horizon exceeded')
    for m in messages:
        if (type(m) is not dict or set(m) != {'role','content'}
                or m['role'] not in ('system','user','assistant') or type(m['content']) is not str):
            raise ValueError('Plain messages required')
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(raw) > MAX_PACKET - 4096:
        raise ValueError('Payload size limit exceeded; never truncate')
    return raw


def validate_key(key):
    if (type(key) is not str or not key or not key.isascii() or len(key) > 1024
            or any(c.isspace() or ord(c) < 32 for c in key)):
        raise ValueError('Invalid credential input')


def redact(text, key):
    # Also redact JSON-escaped occurrences before any response is journaled.
    return text.replace(key, '[REDACTED]').replace(json.dumps(key)[1:-1], '[REDACTED]')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, 'Redirect refused', headers, fp)


def worker_request(packet):
    validate_key(packet['key'])
    key = packet['key']
    data = validate_payload(packet['payload'])
    timeout = packet['timeout']
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 90:
        raise ValueError('Bounded timeout required')
    request = urllib.request.Request(URL, data=data, method='POST',
        headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=min(30, timeout)) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            return {'transport_status':'RESPONSE_TOO_LARGE','billing_unknown':True}
        return {'transport_status':'RESPONSE_RECEIVED',
                'response_text':redact(raw.decode('utf-8'),key)}
    except urllib.error.HTTPError as exc:
        # Do not log request headers, the key, or untrusted error descriptions.
        return {'transport_status':'HTTP_ERROR','http_status':exc.code,'billing_unknown':True}
    except Exception as exc:
        return {'transport_status':'TRANSPORT_ERROR','error_type':type(exc).__name__,
                'billing_unknown':True}


def run_owned(command, packet, timeout):
    """subprocess.run kills/waits for its owned child on TimeoutExpired."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    try:
        result = subprocess.run(command, input=packet, capture_output=True,
                                timeout=timeout, creationflags=flags)
    except subprocess.TimeoutExpired:
        return {'transport_status':'WORKER_TIMEOUT','billing_unknown':True,
                'server_cancellation_verified':False}
    if result.returncode:
        return {'transport_status':'WORKER_FAILED','returncode':result.returncode,
                'billing_unknown':True}
    if len(result.stdout) > MAX_RESPONSE * 2:
        return {'transport_status':'WORKER_OUTPUT_TOO_LARGE','billing_unknown':True}
    try:
        return json.loads(result.stdout.decode('utf-8'))
    except (ValueError, UnicodeError):
        return {'transport_status':'INVALID_WORKER_OUTPUT','billing_unknown':True}


def invoke(payload, key, timeout):
    validate_key(key)
    validate_payload(payload)
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 90:
        raise ValueError('Timeout must be within 90 seconds')
    packet = json.dumps({'payload':payload,'key':key,'timeout':timeout},
                        ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(packet) > MAX_PACKET:
        raise ValueError('Worker packet exceeds size limit')
    result = run_owned([sys.executable,str(Path(__file__).resolve()),'--worker'], packet, timeout)
    return json.loads(redact(json.dumps(result), key))


class OfficialTransport:
    """One owner, no resumption. 'save' must be an exclusive journal writer."""
    def __init__(self, key, save):
        validate_key(key)
        self._key, self.save = key, save
        self.count, self.failed = 0, False

    def __call__(self, payload, timeout):
        if self.failed:
            raise RuntimeError('Transport terminal; do not retry')
        self.count += 1
        try:
            record = invoke(payload, self._key, timeout)
            self.save(f'transport-{self.count:03d}', record)
            if record.get('transport_status') != 'RESPONSE_RECEIVED':
                raise RuntimeError('Official request failed; billing uncertain; no retry')
            raw = json.loads(record['response_text'])
            if type(raw) is not dict:
                raise ValueError('Expected provider JSON object; raw text retained')
            return raw
        except BaseException:
            self.failed = True
            raise


def main():
    if sys.argv[1:] != ['--worker']:
        raise SystemExit('No standalone inference command; fixed authorized launcher required')
    raw = sys.stdin.buffer.read(MAX_PACKET + 1)
    try:
        if len(raw) > MAX_PACKET: raise ValueError('Worker packet too large')
        result = worker_request(json.loads(raw))
    except Exception as exc:
        result = {'transport_status':'WORKER_VALIDATION_FAILED','error_type':type(exc).__name__,
                  'billing_unknown':False,'request_dispatched':False}
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__': main()
