# -*- coding: utf-8 -*-
"""Classify models into KEEP / ARCHIVE / DELETE given what the 8090 slot actually runs.

READ-ONLY analysis. Nothing is deleted here; this only produces the numbers for
the report. The key insight the raw scan misses: "referenced by a .bat" is not
the same as "needed" -- port 8090 is now occupied by Strata, so every
llama.cpp alternative launcher is a dormant fallback, and the weights behind
them are the bulk of the disk.
"""
import json
import os
import subprocess
import sys

ROOT = r"D:\MyLLM"
MODELS = os.path.join(ROOT, "models")
GIB = 1 << 30

# what 8090 runs right now
LIVE_CFG = os.path.join(ROOT, "strata-ornith-nvfp4-8090.json")

# Strata 8090 needs exactly these (model + tokenizer + CPU vision encoder)
KEEP_EXACT = {
    "Ornith-1.5-35B-A3B-NVFP4-q36.gguf",
    "mmproj-Ornith-1.5-35B-BF16.gguf",
    "version.txt",
}
KEEP_DIRS = {"ornith-nvfp4"}          # tokenizer for the live config

# the offline expert pool: verified byte-for-byte, but NOT wired into the config
# yet (the parity script never ran because VRAM was busy). Cheap to rebuild
# (84 s) -- keep only if the user still wants that optimisation.
POOL = "experts-q36.bin"

# dormant llama.cpp launchers: script exists, weights behind it
ARCHIVE_BECAUSE_FALLBACK = {
    "Ornith-1.5-35B-A3B-NVFP4.gguf":       "8090_ornith_nvfp4_cpu18.bat",
    "Ornith-1.5-35B-Q4_K_M.gguf":          "8090_ornith_q4km_cpu16.bat",
    "Ornith-1.5-35B-A3B-APEX-MTP-I-Compact.gguf": "8090_ornith_apex_cpu8.bat",
    "ornith-official":                      "8090_ornith_iq4xs_cpu12.bat (IQ4_XS inside)",
    "Qwen3.8-27B-UD-IQ3_S.gguf":           "start_qwen38_27b_ud_8090.bat",
    "bonsai_mtp":                           "start_bonsai_mtp_8090.bat (+3 more)",
    "LFM2.5-24B-A2B":                      "start_lfm25_q4a_8090.bat / udq4xl",
    "Ornith-1.5-122B":                     "start_ornith122b_8093.bat (port 8093)",
}

# no launcher points at these at all
TRUE_ORPHANS = {
    "MiniCPM-V-4.7-35B-A3B-split",
    "LFM2.5-24B-A2B-split",
    "GLM-OCR-Q8_0.gguf",
    "mmproj-GLM-OCR-Q8_0.gguf",
    "Ornith-1.5-35B-A3B-DFlash-bf16.gguf",
    "hub",
}


def gib(n: int) -> float:
    return n / GIB


def size_of(p: str) -> int:
    if os.path.isfile(p):
        return os.path.getsize(p)
    total = 0
    for root, _d, files in os.walk(p):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def free_gb() -> float:
    out = subprocess.run(["df", "-k", ROOT], capture_output=True).stdout.decode()
    return int(out.splitlines()[1].split()[3]) / (1 << 20)


def main() -> None:
    with open(LIVE_CFG, "rb") as f:
        cfg = json.loads(f.read().decode("utf-8"))
    args = cfg["args"]

    print(f"free on D: {free_gb():.1f} GiB")
    print(f"live: {os.path.basename(args[args.index('--model') + 1])}"
          f"  ctx={args[args.index('--max-context') + 1]}"
          f"  kv={args[args.index('--kv') + 1]}"
          f"  slots={args[args.index('--expert-slots') + 1]}")
    print()

    keep = pool = archive = orphan = 0.0

    print("=== KEEP: Strata 8090 needs these ===")
    for name in sorted(KEEP_EXACT):
        p = os.path.join(MODELS, name)
        if os.path.exists(p):
            s = size_of(p)
            keep += s
            print(f"  {gib(s):8.2f}G  {name}")
    for d in sorted(KEEP_DIRS):
        p = os.path.join(MODELS, d)
        if os.path.isdir(p):
            s = size_of(p)
            keep += s
            print(f"  {gib(s):8.2f}G  {d}{os.sep}  (tokenizer)")
    print(f"  {'':>9}  ------------------")
    print(f"  {keep / GIB:8.2f}G  subtotal -- DO NOT TOUCH")
    print()

    p = os.path.join(MODELS, POOL)
    if os.path.exists(p):
        pool = size_of(p)
        print("=== DECIDE: offline expert pool (verified, not yet in production) ===")
        print(f"  {gib(pool):8.2f}G  {POOL}")
        print("       byte-verified 10,496/10,496 blobs, but --expert-pool is NOT in the config;")
        print("       rebuildable in ~84 s. Deleting it costs nothing but the startup-time win.")
        print()

    print("=== ARCHIVE-CANDIDATES: dormant llama.cpp fallback launchers ===")
    print("    (port 8090 is Strata's now; these weights are for the old fallback tier)")
    for name, why in sorted(ARCHIVE_BECAUSE_FALLBACK.items(),
                            key=lambda kv: -size_of(os.path.join(MODELS, kv[0]))):
        p = os.path.join(MODELS, name)
        if os.path.exists(p):
            s = size_of(p)
            archive += s
            print(f"  {gib(s):8.2f}G  {name}")
            print(f"             {'':9}  via {why}")
    print(f"  {'':>9}  ------------------")
    print(f"  {archive / GIB:8.2f}G  subtotal")
    print()

    print("=== TRUE ORPHANS: no launcher anywhere points here ===")
    for name in sorted(TRUE_ORPHANS, key=lambda n: -size_of(os.path.join(MODELS, n))):
        p = os.path.join(MODELS, name)
        if os.path.exists(p):
            s = size_of(p)
            orphan += s
            print(f"  {gib(s):8.2f}G  {name}")
    print(f"  {'':>9}  ------------------")
    print(f"  {orphan / GIB:8.2f}G  subtotal")
    print()

    print("=== summary ===")
    print(f"  keep                {keep / GIB:8.2f}G")
    print(f"  expert pool (opt)   {pool / GIB:8.2f}G")
    print(f"  archive candidates  {archive / GIB:8.2f}G")
    print(f"  true orphans        {orphan / GIB:8.2f}G")
    print(f"  ------------------------------------")
    print(f"  reclaimable now     {(archive + orphan) / GIB:8.2f}G"
          f"  (all but keep)")
    print(f"  reclaimable + pool  {(archive + orphan + pool) / GIB:8.2f}G")

    print()
    print("=== stale build/runtime dirs (outside models/) ===")
    stale = {
        "strata-build": "superseded by strata-build2",
        "strata-build-vs": "empty VS shell",
        "_upgrade": "old llama.cpp upgrade staging (b11539)",
        "llama.cpp.bak_b10566": "APEX runtime backup",
        "llama.cpp.bak_b10783_20261010_1129": "b10783 backup, now on b11539",
        "llama.cpp.prism-b10709": "Prism experiment",
        "llama.cpp-dev": "dev tree",
        "llama.cpp.prism-src": "Prism sources",
    }
    tot = 0.0
    for d, why in stale.items():
        p = os.path.join(ROOT, d)
        if os.path.isdir(p):
            s = size_of(p)
            tot += s
            print(f"  {gib(s):8.2f}G  {d:<34} {why}")
    print(f"  {'':>9}  ------------------")
    print(f"  {tot / GIB:8.2f}G  subtotal (NOTE: llama.cpp\\ itself is NOT here -- the")
    print("       vision encoder needs cublas64_13.dll from it, keep it)")

    print()
    print("=== KEEP at all costs ===")
    print("  llama.cpp\\            0.70G  supplies cublas64_13.dll for strata-vision.exe")
    print("  strata-build2\\        1.99G  the engine the live config points at")
    print("  strata-src\\           0.55G  serve.server + the q36 sources")
    print("  start_strata_8090.bat         the launcher you use now")
    print("  logs\\                          only trim inside, keep the dir")


if __name__ == "__main__":
    sys.exit(main() or 0)