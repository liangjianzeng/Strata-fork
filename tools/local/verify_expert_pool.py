#!/usr/bin/env python3
"""verify_expert_pool.py - check the offline expert pool against the GGUF it was made from.

The pool file must hold, for every (block, expert), exactly the bytes the engine
would build at run time (q36_engine.cu: expert_blob() = [gate | up | down | tail]).
This script rebuilds those bytes from the GGUF, independently of the writer, and
compares them with the file.

    python verify_expert_pool.py --gguf <model>.gguf --pool <pool>.bin
    python verify_expert_pool.py --gguf <model>.gguf --pool <pool>.bin --layers 0,20,40
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import sys

import numpy as np

HEADER = struct.Struct("<4sIIIIIIQQQQ")
LAYER_ENTRY = struct.Struct("<II" + "Q" * 10)
BLOCK_GEOM = {2: (32, 18), 6: (32, 22), 7: (32, 24), 8: (32, 34), 3: (32, 20),
               12: (256, 144), 13: (256, 176), 14: (256, 210), 11: (256, 110),
               40: (64, 36)}
NVFP4 = 40


def row_bytes(t: int, n: int) -> int:
    be, bb = BLOCK_GEOM[t]
    assert n % be == 0, (t, n)
    return (n // be) * bb


def lcm(a: int, b: int) -> int:
    return a * b // __import__("math").gcd(a, b)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--pool", required=True)
    ap.add_argument("--layers", default="", help="only these blocks (comma separated)")
    args = ap.parse_args()

    from gguf import GGUFReader
    reader = GGUFReader(args.gguf, "r")
    tensors = {t.name: t for t in reader.tensors}

    with open(args.pool, "rb") as f:
        head = f.read(HEADER.size)
        magic, version, flags, n_layers, n_expert, n_embd, n_ff, n_main, table_off, data_off, total = \
            HEADER.unpack(head)
        assert magic == b"SQEP", magic
        assert version == 1
        print("verify: pool %u blocks (main %u + mtp %u), %u experts, embd %u, ff %u, %.2f GiB"
              % (n_layers, n_main, n_layers - n_main, n_expert, n_embd, n_ff, total / 2 ** 30))
        f.seek(table_off)
        table = [LAYER_ENTRY.unpack(f.read(LAYER_ENTRY.size)) for _ in range(n_layers)]

    def raw(name):
        return np.ascontiguousarray(tensors[name].data).view(np.uint8).ravel()

    def scales(name):
        t = tensors.get(name)
        return None if t is None else np.ascontiguousarray(t.data).view(np.float32).ravel()

    pool = open(args.pool, "rb")
    want = [int(x) for x in args.layers.split(",")] if args.layers else list(range(n_layers))
    bad = 0
    checked = 0
    for l in want:
        (gu_type, d_type, gu_row, d_row, up_off, down_off, tail_off, nbytes, stride, blob_off, scale_off,
         scale_n) = table[l]
        # the layout the engine computes, recomputed here
        e_gu_row = row_bytes(gu_type, n_embd)
        e_d_row = row_bytes(d_type, n_ff)
        e_up_off = n_ff * e_gu_row
        e_down_off = 2 * e_up_off
        e_bytes = e_down_off + n_embd * e_d_row
        e_tail = 0
        if gu_type == NVFP4 or d_type == NVFP4:
            e_tail = e_bytes
            e_bytes += 16
        e_al = lcm(lcm(BLOCK_GEOM[gu_type][1], BLOCK_GEOM[d_type][1]), 16)
        e_stride = (e_bytes + e_al - 1) // e_al * e_al
        if (gu_row, d_row, up_off, down_off, tail_off, nbytes, stride) != \
           (e_gu_row, e_d_row, e_up_off, e_down_off, e_tail, e_bytes, e_stride):
            print("verify: block %d geometry differs from the engine's layout" % l)
            bad += 1
            continue
        g = raw("blk.%d.ffn_gate_exps.weight" % l)
        u = raw("blk.%d.ffn_up_exps.weight" % l)
        d = raw("blk.%d.ffn_down_exps.weight" % l)
        sg = scales("blk.%d.ffn_gate_exps.scale" % l)
        su = scales("blk.%d.ffn_up_exps.scale" % l)
        sd = scales("blk.%d.ffn_down_exps.scale" % l)
        blob = np.zeros(e_stride, dtype=np.uint8)
        hb = hashlib.sha256()
        for e in range(n_expert):
            blob[:e_up_off] = g[e * e_up_off:(e + 1) * e_up_off]
            blob[e_up_off:2 * e_up_off] = u[e * e_up_off:(e + 1) * e_up_off]
            blob[e_down_off:e_down_off + n_embd * e_d_row] = d[e * n_embd * e_d_row:(e + 1) * n_embd * e_d_row]
            if e_tail:
                blob[e_tail:e_tail + 16] = np.array([sg[e], su[e], sd[e], 0.0], dtype=np.float32).view(np.uint8)
            pool.seek(blob_off + e * stride)
            got = pool.read(e_bytes)
            want_b = blob[:e_bytes].tobytes()
            if got != want_b:
                if bad < 5:
                    h1 = hashlib.sha256(got).hexdigest()[:16]
                    h2 = hashlib.sha256(want_b).hexdigest()[:16]
                    first = next((i for i in range(min(len(got), len(want_b))) if got[i] != want_b[i]), -1)
                    print("verify: block %d expert %d differs (file %s vs gguf %s, first byte %d)"
                          % (l, e, h1, h2, first))
                bad += 1
            hb.update(want_b)
            checked += 1
        print("verify: block %2d %-5s  %4d experts  %s" % (l, "mtp" if l >= n_main else "main", n_expert,
                                                             hb.hexdigest()[:32]))
    pool.close()
    print("verify: %d expert blobs compared, %d differ" % (checked, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
