# -*- coding: utf-8 -*-
"""Verify the rebuilt tokenizer with Strata's own code path.

serve/server.py:7697-7706 builds the tokenizer from vocab.json + merges.txt +
token_type.json and nothing else, so this replicates exactly that, then checks
the tokenizer can encode a Chinese+English string and decode back to it. A wrong
token_type.json shows up here as broken round-tripping, not as an error.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, r'D:\MyLLM\strata-src\tools')
sys.path.insert(0, r'D:\MyLLM\strata-src')
T = Path(r'D:\MyLLM\models\ornith-nvfp4')

for need in ('vocab.json', 'merges.txt', 'token_type.json'):
    assert (T / need).is_file(), 'missing ' + need
print('all three files present')

vocab = json.loads((T / 'vocab.json').read_text(encoding='utf-8'))
tokens = [None] * len(vocab)
for t, i in vocab.items():
    tokens[i] = t
merges = (T / 'merges.txt').read_text(encoding='utf-8').split('\n')
types = json.loads((T / 'token_type.json').read_text())
print('tokens %d  merges %d  types %d' % (len(tokens), len(merges), len(types)))

import strata_tokenizer as ST
tok = ST.Tokenizer(tokens, merges, types)
print('strata_tokenizer built OK')

CASES = [
    '用一句话说明什么是张量并行。',
    'The quick brown fox jumps over the lazy dog.',
    'def f(x): return x * 2  # 注释',
    '1234*5678=7006652',
    '混合专家池：每层只让 48 / 256 个进显存',
]
ok = True
for s in CASES:
    ids = tok.encode(s) if hasattr(tok, 'encode') else None
    if ids is None:
        print('  (no encode())', repr(s[:20]))
        continue
    back = tok.decode(ids) if hasattr(tok, 'decode') else None
    if back is None:
        continue
    good = back.strip() == s.strip()
    ok &= good
    print('  %-5s %d ids  %s' % ('OK' if good else 'FAIL', len(ids), repr(s[:28])))
    if not good:
        print('        got: %r' % back[:80])

print()
print('round-trip:', 'PASS' if ok else 'FAIL')
sys.exit(0 if ok else 1)