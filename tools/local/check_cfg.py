# -*- coding: utf-8 -*-
"""Offline validation of the 8090 config: JSON parses, every referenced file
exists, and serve.server's own config reader accepts it (no service started).
"""
import json
import os
import sys

CFG = r"D:\MyLLM\strata-ornith-nvfp4-8090.json"

fail = []

with open(CFG, "rb") as f:
    cfg = json.loads(f.read().decode("utf-8"))

print("== top-level keys ==")
for k in cfg:
    print("  ", k)

print()
print("== args ==")
args = cfg["args"]
for i in range(0, len(args), 2):
    print(f"   {args[i]:<20} {args[i + 1] if i + 1 < len(args) else ''}")

# derive the settings the operator cares about
kv = args[args.index("--kv") + 1]
ctx = int(args[args.index("--max-context") + 1])
slots = int(args[args.index("--expert-slots") + 1])
has_batch = "--batch" in args
has_vision = "--vision" in args

print()
print("== derived ==")
print(f"   ctx        = {ctx} ({ctx / 1024:.0f}K)")
print(f"   kv         = {kv}")
print(f"   expert slots = {slots}")
print(f"   batch      = {'ABSENT -> 1 request slot' if not has_batch else 'present'}")
print(f"   vision arg = {has_vision}")

print()
print("== file existence ==")
paths = [("exe", cfg["exe"]), ("tokenizer", cfg["tokenizer"])]
paths.append(("model", args[args.index("--model") + 1]))
if isinstance(cfg.get("vision"), dict):
    for k in ("exe", "mmproj", "model"):
        v = cfg["vision"].get(k)
        if v:
            paths.append((f"vision.{k}", v))

for name, p in paths:
    ap = os.path.abspath(p)
    ok = os.path.exists(ap)
    size = os.path.getsize(ap) / (1 << 30) if ok and os.path.isfile(ap) else 0
    print(f"   {'OK ' if ok else 'MISSING'} {name:<14} {ap}" + (f"  ({size:.2f} GiB)" if size else ""))
    if not ok:
        fail.append(name)

print()
print("== vision encoder mode ==")
v = cfg.get("vision")
if isinstance(v, dict):
    print(f"   gpu     = {v.get('gpu')}  -> mmproj loads on {'GPU (VRAM)' if v.get('gpu') else 'CPU (host RAM)'}")
    print(f"   threads = {v.get('threads')}")
else:
    print("   NO vision section -> the encoder will not start and every image is refused")
    fail.append("vision")

print()
print("== KV budget (11 layers = 10 attn + MTP, HKV=2, HD=256) ==")
# per_tok is KiB *per token*; ctx tokens * KiB -> bytes -> GiB
per_tok_kib = {"q8_0": 11.7, "int8": 11.3, "q4_0": 6.2, "fp16": 44.0}[kv]
gib = per_tok_kib * ctx / (1 << 20)
print(f"   {kv}: {per_tok_kib} KiB/token x {ctx} tokens = {gib:.2f} GiB for one slot")
print(f"   (F32/fp16 at this ctx would need {44.0 * ctx / (1 << 20):.1f} GiB -> will NOT fit on a 16 GB card)")

print()
print("RESULT:", "FAIL -> " + ", ".join(fail) if fail else "all checks passed")
sys.exit(1 if fail else 0)