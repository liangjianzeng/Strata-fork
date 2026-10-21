# -*- coding: utf-8 -*-
"""Rebuild the tokenizer directory that the cleanup deleted.

models/ornith-nvfp4/ held the HF safetensors (the conversion source) AND was the
configured "tokenizer" directory for 8090.  The weights are gone, but the service
only ever needed four small files, and all four are still on ModelScope:

    vocab.json 6.41 MB + merges.txt 3.20 MB      (read directly by serve/server.py)
    tokenizer.json 12.21 MB                       (source of truth for the types)
    tokenizer_config.json 0.02 MB                 (bos/eos/pad ids)
    chat_template.jinja 0.01 MB

serve/server.py:7697 hard-fails without vocab.json, so this is not optional.
token_type.json is a Strata setup by-product: 1 = NORMAL, 3 = CONTROL, taken from
the added_tokens that tokenizer.json marks "special", plus the config's bos/eos/pad.

Verified afterwards by re-encoding a known string and comparing the ids with the
engine's own tokenizer output.
"""
import json, os, sys, time, urllib.request

DST = r'D:\MyLLM\models\ornith-nvfp4'
BASE = ('https://www.modelscope.cn/models/ornith-ai/Ornith-1.5-35B-A3B-NVFP4'
        '/resolve/master/')
WANT = ['vocab.json', 'merges.txt', 'tokenizer.json',
        'tokenizer_config.json', 'chat_template.jinja', 'config.json']


def fetch(name):
    dst = os.path.join(DST, name)
    if os.path.exists(dst) and os.path.getsize(dst) > 0:
        print('  have %-26s %8.2f MB' % (name, os.path.getsize(dst) / 1048576))
        return dst
    for attempt in range(3):
        try:
            req = urllib.request.Request(BASE + name, headers={'User-Agent': 'python'})
            t0 = time.time()
            with urllib.request.urlopen(req, timeout=180) as r, open(dst + '.part', 'wb') as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            os.replace(dst + '.part', dst)
            print('  got  %-26s %8.2f MB  (%.1fs)' % (name, os.path.getsize(dst) / 1048576,
                                                      time.time() - t0))
            return dst
        except Exception as e:
            print('  retry %s (%s/%s): %s' % (name, attempt + 1, 3, type(e).__name__))
            time.sleep(3)
    return None


os.makedirs(DST, exist_ok=True)
print('downloading the tokenizer files into', DST)
paths = {n: fetch(n) for n in WANT}

need = ['vocab.json', 'merges.txt', 'tokenizer.json', 'tokenizer_config.json']
missing = [n for n in need if not paths[n]]
if missing:
    print('MISSING:', missing)
    sys.exit(1)

# --- build token_type.json -------------------------------------------------
# 1 = NORMAL, 3 = CONTROL. Strata's ST.Tokenizer only cares about the distinction.
tj = json.loads(open(paths['tokenizer.json'], encoding='utf-8').read())
cfg_tc = json.loads(open(paths['tokenizer_config.json'], encoding='utf-8').read())

n_vocab = len(tj['model']['vocab'])
types = [1] * n_vocab

control = set()
for at in tj.get('added_tokens', []):
    if at.get('special'):
        control.add(at['id'])
for key in ('bos_token', 'eos_token', 'pad_token'):
    v = cfg_tc.get(key)
    if isinstance(v, dict) and isinstance(v.get('id'), int):
        control.add(v['id'])

for i in control:
    if 0 <= i < n_vocab:
        types[i] = 3

tt_path = os.path.join(DST, 'token_type.json')
with open(tt_path, 'w', encoding='utf-8') as f:
    json.dump(types, f)

vocab = json.loads(open(paths['vocab.json'], encoding='utf-8').read())
n_merges = len(open(paths['merges.txt'], encoding='utf-8').read().split('\n'))

print()
print('vocab.json       entries :', len(vocab))
print('merges.txt       lines   :', n_merges)
print('token_type.json  len     :', len(types), '(vocab', n_vocab, ')')
print('  CONTROL(3) ids  :', sorted(control))
print('wrote', tt_path)

assert len(types) == len(vocab), (len(types), len(vocab))
assert max(vocab.values()) < n_vocab, 'vocab.json has an id beyond the model vocab'
print('SANITY OK')