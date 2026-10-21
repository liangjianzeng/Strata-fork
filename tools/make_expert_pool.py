#!/usr/bin/env python3
"""make_expert_pool.py - the offline expert pool for the Strata q36 engine.

The q36 engine places every routed expert in host memory as a native blob

    [ gate | up | down | tail(16 B) ]

and, in the hybrid mode, keeps a copy of that pool in pinned memory
(cudaHostAlloc).  Building it costs a 17.7 GiB memcpy at every start: the
engine walks the GGUF, copies each expert out of the mapped file into the
pinned arena, and that copy is what costs the 45.7 s.

This script does that work once, offline, and writes the result to a single
file in the layout the engine already expects.  The engine's --expert-pool
then maps the file and points the pool at it: no copy, no pinned memory, and
the GGUF's expert pages never have to be resident.

The layout written here is byte for byte the layout the engine builds at run
time (q36_engine.cu: expert_blob() and native_expert_layout()), so the two
paths produce identical outputs.  The file is self-describing: the header and
the per-layer table carry the geometry, and the engine refuses the file if any
of it disagrees with the GGUF it was built for.

    python make_expert_pool.py --gguf <model>.gguf --out <pool>.bin
    python make_expert_pool.py --gguf <model>.gguf --out <pool>.bin --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import math
import os
import struct
import sys
import time

import numpy as np

MAGIC = b"SQEP"
VERSION = 1

# GGML type -> (block elements, block bytes).  The three the engine's native
# expert path uses: NVFP4 (40), Q8_0 (8), Q4_0 (2).  Mirrors
# strata::block_geometry() in include/strata/artifact/gguf_reader.hpp.
BLOCK_GEOM = {
    2: (32, 18),     # Q4_0
    6: (32, 22),     # Q5_0
    7: (32, 24),     # Q5_1
    8: (32, 34),     # Q8_0
    3: (32, 20),     # Q4_1
    12: (256, 144),  # Q4_K
    13: (256, 176),  # Q5_K
    14: (256, 210),  # Q6_K
    11: (256, 110),  # Q3_K
    40: (64, 36),    # NVFP4: 4 UE4M3 sub-block scales + 32 B of E2M1 codes
}
NVFP4 = 40


def block_bytes(t: int) -> int:
    if t not in BLOCK_GEOM:
        raise SystemExit("pool: type %d is not in the block table" % t)
    return BLOCK_GEOM[t][1]


def row_bytes(t: int, n: int) -> int:
    be, bb = BLOCK_GEOM[t]
    if n % be:
        raise SystemExit("pool: %d is not a whole number of %d-value blocks" % (n, be))
    return (n // be) * bb


def lcm(a: int, b: int) -> int:
    return a * b // math.gcd(a, b)


def native_expert_layout(gu_type: int, d_type: int, n_embd: int, n_ff: int) -> dict:
    """The engine's layout (iq_kernels.cu: native_expert_layout), exactly."""
    gu_row = row_bytes(gu_type, n_embd)
    d_row = row_bytes(d_type, n_ff)
    up_off = n_ff * gu_row
    down_off = 2 * up_off
    total = down_off + n_embd * d_row
    tail_off = 0
    if gu_type == NVFP4 or d_type == NVFP4:
        tail_off = total
        total += 16
    al = lcm(lcm(block_bytes(gu_type), block_bytes(d_type)), 16)
    stride = (total + al - 1) // al * al
    return dict(gu_type=gu_type, d_type=d_type, gu_row=gu_row, d_row=d_row,
                up_off=up_off, down_off=down_off, tail_off=tail_off,
                bytes=total, stride=stride, src_gsz=up_off, src_dsz=n_embd * d_row)


LAYER_ENTRY = struct.Struct("<II" + "Q" * 10)
HEADER = struct.Struct("<4sIIIIIIQQQQ")


def _field_value(f):
    """A gguf-py ReaderField: one part for a number, two for a string."""
    parts = getattr(f, "parts", None)
    if parts is None:
        return f
    if len(parts) == 1:
        return parts[0][0]
    for p in parts:
        v = p[0]
        if isinstance(v, (bytes, bytearray)):
            return v.decode("utf-8", "replace")
        if isinstance(v, str):
            return v
    return parts[-1][0]


def read_model_geometry(reader) -> dict:
    """n_layers / n_expert / n_embd / n_ff, from the metadata with the tensor
    shapes as the fallback (the MTP block's experts are 2D and flat)."""
    if hasattr(reader, "metadata"):
        raw = reader.metadata()
    else:                       # gguf-py 0.18+: the fields are a flat dict
        raw = {k: _field_value(v) for k, v in reader.fields.items()}
    meta = {k: _field_value(v) for k, v in raw.items()}

    def m(key, default=None):
        v = meta.get(key)
        return default if v is None else v

    # the architecture prefix: the key that carries block_count
    arch = None
    for k in meta:
        if k.endswith(".block_count") and not k.startswith("general."):
            arch = k[: -len(".block_count")]
            break
    if arch is None:
        arch = "qwen35moe"
    # block_count counts the MTP block too (41 = 40 main layers + 1 MTP block)
    n_blocks = int(m("%s.block_count" % arch, m("general.block_count", 41)))
    n_mtp = int(m("%s.nextn_predict_layers" % arch, 0))
    n_expert = int(m("%s.expert_count" % arch, m("general.expert_count", 256)))
    n_embd = int(m("%s.embedding_length" % arch, m("general.embedding_length", 2048)))
    n_ff = int(m("%s.expert_feed_forward_length" % arch,
                 m("%s.expert_shared_feed_forward_length" % arch, 512)))
    return dict(arch=arch, n_layers=n_blocks, n_main=n_blocks - n_mtp,
                n_expert=n_expert, n_embd=n_embd, n_ff=n_ff, n_mtp=n_mtp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gguf", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--dry-run", action="store_true", help="print the layout and stop")
    args = ap.parse_args()

    try:
        from gguf import GGUFReader
    except ImportError:
        print("pool: the gguf package is missing (pip install gguf)", file=sys.stderr)
        return 2

    t0 = time.time()
    print("pool: reading", args.gguf)
    reader = GGUFReader(args.gguf, "r")
    geo = read_model_geometry(reader)
    n_layers, n_expert, n_embd, n_ff = geo["n_layers"], geo["n_expert"], geo["n_embd"], geo["n_ff"]
    n_mtp = geo["n_mtp"]
    n_main = geo["n_main"]
    total_layers = n_layers
    print("pool: arch=%s blocks=%d (main %d + mtp %d) experts=%d embd=%d ff=%d"
          % (geo["arch"], n_layers, n_main, n_mtp, n_expert, n_embd, n_ff))

    tensors = {t.name: t for t in reader.tensors}

    def find(name):
        t = tensors.get(name)
        if t is None:
            raise SystemExit("pool: the model has no tensor " + name)
        return t

    def raw(name) -> np.ndarray:
        """The tensor's bytes as a flat uint8 view (C order == the GGUF's ne
        order, ne[0] fastest, so a byte offset is a byte offset)."""
        t = find(name)
        d = np.ascontiguousarray(t.data).view(np.uint8).ravel()
        return d

    def scale_array(name) -> np.ndarray | None:
        t = tensors.get(name)
        if t is None:
            return None
        return np.ascontiguousarray(t.data).view(np.float32).ravel()

    # ---- the per-layer geometry -------------------------------------------------
    layers = []
    for l in range(total_layers):
        is_mtp = l >= n_main
        g = find("blk.%d.ffn_gate_exps.weight" % l)
        u = find("blk.%d.ffn_up_exps.weight" % l)
        d = find("blk.%d.ffn_down_exps.weight" % l)
        gt, ut, dt = int(g.tensor_type), int(u.tensor_type), int(d.tensor_type)
        if gt != ut:
            raise SystemExit("pool: layer %d gate/up differ in type" % l)
        lay = native_expert_layout(gt, dt, n_embd, n_ff)
        lay["layer"] = l
        lay["is_mtp"] = is_mtp
        # the byte counts the engine's own copy would read
        lay["gate_bytes"] = n_ff * lay["gu_row"] * n_expert
        lay["up_bytes"] = lay["gate_bytes"]
        lay["down_bytes"] = n_embd * lay["d_row"] * n_expert
        # the experts of the MTP block are stored flat (2D); the others are 3D with
        # the expert axis last.  Either way expert e is one contiguous run.
        for nm, want in (("ffn_gate_exps.weight", lay["gate_bytes"]),
                         ("ffn_up_exps.weight", lay["up_bytes"]),
                         ("ffn_down_exps.weight", lay["down_bytes"])):
            got = raw("blk.%d.%s" % (l, nm)).size
            if got != want:
                raise SystemExit("pool: layer %d %s is %d B, the layout wants %d B"
                                 % (l, nm, got, want))
        sg = scale_array("blk.%d.ffn_gate_exps.scale" % l)
        su = scale_array("blk.%d.ffn_up_exps.scale" % l)
        sd = scale_array("blk.%d.ffn_down_exps.scale" % l)
        if lay["tail_off"]:
            for nm, arr in (("gate", sg), ("up", su), ("down", sd)):
                if arr is None:
                    raise SystemExit("pool: layer %d is NVFP4 but has no %s scale" % (l, nm))
                if arr.size != n_expert:
                    raise SystemExit("pool: layer %d %s scale has %d entries, expected %d"
                                     % (l, nm, arr.size, n_expert))
        lay["scales"] = (sg, su, sd) if lay["tail_off"] else (None, None, None)
        layers.append(lay)
        print("pool: layer %2d %-5s gu=%d d=%d gu_row=%d d_row=%d up_off=%d down_off=%d tail=%d bytes=%d stride=%d"
              % (l, "mtp" if is_mtp else "main", gt, dt, lay["gu_row"], lay["d_row"],
                 lay["up_off"], lay["down_off"], lay["tail_off"], lay["bytes"], lay["stride"]))

    # ---- where everything goes --------------------------------------------------
    header_bytes = HEADER.size
    table_off = header_bytes
    table_bytes = len(layers) * LAYER_ENTRY.size
    data_off = table_off + table_bytes
    # the blob area: one layer after the other, each expert at its stride
    off = data_off
    for lay in layers:
        off = (off + 4095) // 4096 * 4096
        lay["blob_off"] = off
        off += lay["stride"] * n_expert
    # the per-expert scales (F32) after the blobs
    for lay in layers:
        if lay["tail_off"]:
            off = (off + 63) // 64 * 64
            lay["scale_off"] = off
            off += 12 * n_expert
        else:
            lay["scale_off"] = 0
    total_bytes = off
    print("pool: file size %.2f GiB (data at %.2f GiB)"
          % (total_bytes / 2 ** 30, data_off / 2 ** 30))

    if args.dry_run:
        return 0

    # ---- write it ---------------------------------------------------------------
    tmp = args.out + ".tmp"
    with open(tmp, "wb") as f:
        f.write(b"\0" * data_off)
        blob = np.zeros(max(lay["stride"] for lay in layers), dtype=np.uint8)
        for lay in layers:
            g = raw("blk.%d.ffn_gate_exps.weight" % lay["layer"])
            u = raw("blk.%d.ffn_up_exps.weight" % lay["layer"])
            d = raw("blk.%d.ffn_down_exps.weight" % lay["layer"])
            f.seek(lay["blob_off"])
            for e in range(n_expert):
                gsl = slice(e * lay["src_gsz"], (e + 1) * lay["src_gsz"])
                usl = slice(e * lay["src_gsz"], (e + 1) * lay["src_gsz"])
                dsl = slice(e * lay["src_dsz"], (e + 1) * lay["src_dsz"])
                blob[:lay["src_gsz"]] = g[gsl]
                blob[lay["up_off"]:lay["up_off"] + lay["src_gsz"]] = u[usl]
                blob[lay["down_off"]:lay["down_off"] + lay["src_dsz"]] = d[dsl]
                if lay["tail_off"]:
                    sg, su, sd = lay["scales"]
                    tail = np.array([sg[e], su[e], sd[e], 0.0], dtype=np.float32)
                    blob[lay["tail_off"]:lay["tail_off"] + 16] = tail.view(np.uint8)
                f.write(memoryview(blob[:lay["bytes"]]).cast("B"))
                # the stride padding must be zero: MMQ steps over the experts by
                # blocks and reads a little past the last one
                pad = lay["stride"] - lay["bytes"]
                if pad:
                    f.write(b"\0" * pad)
            if lay["tail_off"]:
                sg, su, sd = lay["scales"]
                f.seek(lay["scale_off"])
                f.write(sg.astype(np.float32).tobytes())
                f.write(su.astype(np.float32).tobytes())
                f.write(sd.astype(np.float32).tobytes())
            print("pool: layer %2d written" % lay["layer"])

        # the header and the table last: a file without them is not finished
        f.seek(table_off)
        for lay in layers:
            f.write(LAYER_ENTRY.pack(
                lay["gu_type"], lay["d_type"], lay["gu_row"], lay["d_row"],
                lay["up_off"], lay["down_off"], lay["tail_off"], lay["bytes"],
                lay["stride"], lay["blob_off"], lay["scale_off"], n_expert))
        f.seek(0)
        f.write(HEADER.pack(MAGIC, VERSION, 0, total_layers, n_expert, n_embd, n_ff,
                            n_layers, table_off, data_off, total_bytes))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, args.out)

    size = os.path.getsize(args.out)
    h = hashlib.sha256()
    with open(args.out, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 24), b""):
            h.update(chunk)
    print("pool: wrote %s  %.2f GiB  in %.1f s" % (args.out, size / 2 ** 30, time.time() - t0))
    print("pool: sha256 %s" % h.hexdigest())
    return 0


if __name__ == "__main__":
    sys.exit(main())
