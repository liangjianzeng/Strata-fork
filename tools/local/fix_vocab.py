# -*- coding: utf-8 -*-
"""Fold the added_tokens into vocab.json so types and tokens line up.

ST.Tokenizer (tools/strata_tokenizer.py:107) does `self.special_tokens[tokens[i]] = i`
for every token_types entry, so token_type.json must be exactly as long as the
vocab list server.py builds from vocab.json. ModelScope's vocab.json holds only the
248044 base merges; the 26 added_tokens (ids 248044-248057+, 14 of them special)
live in tokenizer.json and are absent. Left out, the specials are unreachable and
the indexing runs off the end of the list.

So: pad vocab.json with the added tokens and write token_type.json over the same
248070 range. Both files are the only ones serve/server.py:7699-7705 reads.
"""
import json
from pathlib import Path

T = Path(r'D:\MyLLM\models\ornith-nvfp4')

tj = json.loads((T / 'tokenizer.json').read_text(encoding='utf-8'))
vj = json.loads((T / 'vocab.json').read_text(encoding='utf-8'))
tc = json.loads((T / 'tokenizer_config.json').read_text(encoding='utf-8'))

base = tj['model']['vocab']
added = tj.get('added_tokens', [])

vocab = dict(vj)
n_before = len(vocab)

# added_tokens carry their id; write them in id order so the list index == the id
for at in sorted(added, key=lambda a: a['id']):
    vocab[at['content']] = at['id']

n_total = len(base) + len(added)
assert len(vocab) == n_total, (len(vocab), n_total)

types = [1] * n_total
control = set()
for at in added:
    if at.get('special'):
        control.add(at['id'])
for key in ('bos_token', 'eos_token', 'pad_token'):
    v = tc.get(key)
    if isinstance(v, dict) and isinstance(v.get('id'), int):
        control.add(v['id'])

for i in control:
    if 0 <= i < n_total:
        types[i] = 3

(T / 'vocab.json').write_text(json.dumps(vocab, ensure_ascii=False), encoding='utf-8')
(T / 'token_type.json').write_text(json.dumps(types), encoding='utf-8')

print('vocab.json     %d -> %d entries (+%d added tokens)' % (n_before, len(vocab), len(vocab) - n_before))
print('token_type.json len %d' % len(types))
print('  NORMAL(1)  %d' % types.count(1))
print('  CONTROL(3) %d  ids=%s' % (types.count(3), sorted(control)))
print()
print('eos_token from tokenizer_config:', tc.get('eos_token'))
print('control token strings:')
inv = {i: t for t, i in vocab.items()}
for i in sorted(control)[:6]:
    print('   %d  %s' % (i, inv[i]))
print('   ... (%d total)' % len(control))