import io
import struct
import unittest
from scripts.inspect_reuse_gguf import inspect


def string(value):
    encoded=value.encode();return struct.pack('<Q',len(encoded))+encoded


class Tests(unittest.TestCase):
    def test_template_and_skipped_array(self):
        data=b'GGUF'+struct.pack('<IQQ',3,0,2)
        data+=string('tokenizer.chat_template')+struct.pack('<I',8)+string('complete system {{ messages }}')
        data+=string('tokens')+struct.pack('<IIQ',9,8,2)+string('one')+string('two')
        result=inspect(io.BytesIO(data))
        self.assertEqual(result['metadata']['tokenizer.chat_template'],'complete system {{ messages }}')
        self.assertEqual(result['metadata']['tokens']['elements'],2)
        self.assertFalse(result['tensor_data_read'])

    def test_invalid_or_truncated_header(self):
        for data in (b'bad!',b'GGUF',b'GGUF'+struct.pack('<I',99),
                     b'GGUF'+struct.pack('<IQQ',3,0,5000)):
            with self.assertRaises(ValueError):inspect(io.BytesIO(data))

    def test_duplicate_keys(self):
        field=string('x')+struct.pack('<II',4,5)
        with self.assertRaises(ValueError):inspect(io.BytesIO(b'GGUF'+struct.pack('<IQQ',3,0,2)+field+field))


if __name__=='__main__':unittest.main()
