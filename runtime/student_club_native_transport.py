"""Fixed official-beta native HTTP worker; not an experiment authorization.

Key crosses only owned stdin, never argv/files/logs. All payloads pass the new
profile validator. No redirect, automatic retry, custom endpoint or proxy.
The approved frozen owner must enforce source admission and shared accounting.
"""
import json
import sys
import urllib.error
import urllib.request

from runtime.deepseek_official_transport import (
    MAX_PACKET,MAX_RESPONSE,NoRedirect,redact,run_owned,validate_key)
from runtime.deepseek_native_transport import validate_timeout
from runtime.native_action_interface import ENDPOINT
from runtime.student_club_native_wire import validate_wire

PROFILE='student-club-native-transport-v1'
MODULE='runtime.student_club_native_transport'


def encode_payload(payload):
    validate_wire(payload)
    raw=json.dumps(payload,ensure_ascii=False,allow_nan=False).encode()
    if len(raw)>MAX_PACKET-4096:raise ValueError('Payload too large; no truncation')
    return raw


def worker_request(packet):
    if (type(packet) is not dict or set(packet)!={'profile','key','payload','timeout'}
            or packet['profile']!=PROFILE):
        raise ValueError('Explicit profile required')
    validate_key(packet['key']);validate_timeout(packet['timeout'])
    key=packet['key']
    request=urllib.request.Request(ENDPOINT,data=encode_payload(packet['payload']),method='POST',
        headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    try:
        with opener.open(request,timeout=min(30,packet['timeout'])) as response:
            raw=response.read(MAX_RESPONSE+1)
        if len(raw)>MAX_RESPONSE:return dict(transport_status='RESPONSE_TOO_LARGE',billing_unknown=True)
        return dict(transport_status='RESPONSE_RECEIVED',response_text=redact(raw.decode('utf-8'),key))
    except urllib.error.HTTPError as error:
        return dict(transport_status='HTTP_ERROR',http_status=error.code,billing_unknown=True)
    except Exception as error:
        return dict(transport_status='TRANSPORT_ERROR',error_type=type(error).__name__,billing_unknown=True)


def invoke(payload,key,timeout):
    validate_key(key);validate_timeout(timeout);encode_payload(payload)
    packet=json.dumps(dict(profile=PROFILE,payload=payload,key=key,timeout=timeout),
        ensure_ascii=False,allow_nan=False).encode()
    if len(packet)>MAX_PACKET:raise ValueError('Worker packet too large')
    result=run_owned([sys.executable,'-B','-m',MODULE,'--worker'],packet,timeout)
    return json.loads(redact(json.dumps(result),key))


class Transport:
    def __init__(self,key,save):
        validate_key(key)
        self._key,self.save=key,save
        self.count,self.failed=0,False

    def __call__(self,payload,timeout):
        if self.failed or self.count>=5:raise RuntimeError('Terminal transport; no retry')
        try:
            self.count+=1
            result=invoke(payload,self._key,timeout)
            self.save(f'transport-{self.count:03d}',result)
            if result.get('transport_status')!='RESPONSE_RECEIVED':
                raise RuntimeError('Transport failed; keep uncertain billing reserve')
            if type(result.get('response_text')) is not str:raise ValueError('Missing raw text')
            return result['response_text']
        except BaseException:
            self.failed=True
            raise


if __name__=='__main__':
    if sys.argv[1:]!=['--worker']:raise SystemExit('Use an approved frozen launcher, not this module')
    raw=sys.stdin.buffer.read(MAX_PACKET+1)
    try:
        if len(raw)>MAX_PACKET:raise ValueError('Packet too large')
        result=worker_request(json.loads(raw))
    except Exception as error:
        result=dict(transport_status='WORKER_VALIDATION_FAILED',error_type=type(error).__name__,
            request_dispatched=False,billing_unknown=False)
    sys.stdout.write(json.dumps(result))
