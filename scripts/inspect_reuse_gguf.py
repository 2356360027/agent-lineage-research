"""Read bounded GGUF v2/v3 little-endian metadata, without loading tensors.

Format reference: https://github.com/ggml-org/ggml/blob/master/docs/gguf.md
This utility inspects the chat template; it neither changes nor executes it.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct

FORMATS={0:'B',1:'b',2:'H',3:'h',4:'I',5:'i',6:'f',7:'?',10:'Q',11:'q',12:'d'}


def inspect(stream):
    boundary=32*1024*1024
    def read(n):
        if n<0 or stream.tell()+n>boundary:raise ValueError('Metadata byte bound')
        data=stream.read(n)
        if len(data)!=n:raise ValueError('Truncated metadata')
        return data
    def unpack(fmt):return struct.unpack('<'+fmt,read(struct.calcsize('<'+fmt)))[0]
    def string(keep=True):
        n=unpack('Q')
        if n>1024*1024:raise ValueError('String bound')
        data=read(n)
        return data.decode('utf-8') if keep else None
    def value(kind,keep=True,depth=0):
        if depth>4:raise ValueError('Array nesting bound')
        if kind in FORMATS:
            v=unpack(FORMATS[kind]);return v if keep else None
        if kind==8:return string(keep)
        if kind==9:
            subtype=unpack('I');count=unpack('Q')
            if count>1000000:raise ValueError('Array count bound')
            for _ in range(count):value(subtype,False,depth+1)
            return {'array_element_type':subtype,'elements':count,'values_omitted':True} if keep else None
        raise ValueError('Unknown metadata value type')
    if read(4)!=b'GGUF':raise ValueError('Not a GGUF file')
    version=unpack('I')
    if version not in (2,3):raise ValueError('Only little-endian GGUF v2/v3 supported')
    tensors=unpack('Q');count=unpack('Q')
    if count>4096:raise ValueError('Metadata key bound')
    metadata={}
    for _ in range(count):
        key=string()
        if key in metadata:raise ValueError('Duplicate metadata key')
        metadata[key]=value(unpack('I'))
    return {'gguf_version':version,'tensor_count':tensors,'metadata_count':count,
            'metadata_end_offset':stream.tell(),'tensor_data_read':False,'metadata':metadata}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--model',type=Path,required=True)
    p.add_argument('--expected-sha256',required=True)
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    with a.model.open('rb') as f:
        digest=hashlib.file_digest(f,'sha256').hexdigest()
        if digest!=a.expected_sha256:raise ValueError('Asset SHA256 mismatch')
        f.seek(0);result=inspect(f)
    result.update(model_sha256=digest,model_bytes=a.model.stat().st_size,model_calls=0)
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,indent=2,ensure_ascii=False)
    print(json.dumps({'status':'METADATA_READ_NO_INFERENCE','output':str(a.output),
        'gguf_version':result['gguf_version'],'template':result['metadata'].get('tokenizer.chat_template'),
        'template_sha256':hashlib.sha256(result['metadata'].get('tokenizer.chat_template','').encode()).hexdigest()},indent=2))


if __name__=='__main__':main()
