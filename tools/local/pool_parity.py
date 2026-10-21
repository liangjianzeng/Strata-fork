# -*- coding: utf-8 -*-
"""Parity and startup cost: the offline expert pool vs the engine's own copy.

With --expert-pool the engine maps the pool file and points the hybrid pool at
it; without it, it walks the GGUF and copies every expert into a pinned arena
at every start.  This drives the engine's own --serve protocol twice with the
same prompt and compares the generated token ids, and reports how long each
run took to become READY (the startup that the pool is meant to shorten).

Usage: pool_parity.py [slots] [n_new] [ctx]
"""
import subprocess, sys, threading, time, os, re

EXE = r"D:\MyLLM\strata-build2\strata.exe"
MODEL = r"D:\MyLLM\models\Ornith-1.5-35B-A3B-NVFP4-q36.gguf"
POOL = r"D:\MyLLM\models\experts-q36.bin"
CHUNK = 2048

slots = int(sys.argv[1]) if len(sys.argv) > 1 else 48
nnew = int(sys.argv[2]) if len(sys.argv) > 2 else 24
CTX = int(sys.argv[3]) if len(sys.argv) > 3 else 160000

ids = [int(x) for x in open(r"D:\MyLLM\_tmp_code\realtok.txt").read().strip().split(",")]
prompt = (ids * (4000 // len(ids) + 1))[:4000]


def run(use_pool):
    tag = "pool" if use_pool else "copy"
    err = open(rf"D:\MyLLM\_tmp_code\poolparity_{tag}.log", "w", encoding="utf-8", errors="replace")
    cmd = [EXE, "--serve", "--model", MODEL, "--max-context", str(CTX), "--kv", "q4_0",
           "--prefill", str(CHUNK), "--mtp", "0", "--batch", "2", "--expert-slots", str(slots)]
    if use_pool:
        cmd += ["--expert-pool", POOL]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True,
                         bufsize=1, encoding="utf-8")

    toks, done, ready = [], False, False

    def reader():
        nonlocal done, ready
        for line in p.stdout:
            if line.startswith("READY"):
                ready = True
            elif line.startswith("T "):
                toks.append(int(line[2:].strip()))
            elif line.startswith("DONE"):
                done = True
                break
            elif line.startswith(("ERR", "FATAL")):
                print("ENGINE:", line.strip(), flush=True)

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    t0 = time.time()
    while not ready and time.time() - t0 < 900:
        if p.poll() is not None:
            print("ENGINE DIED before READY, rc=", p.returncode)
            print(open(err.name, encoding="utf-8", errors="replace").read()[-2000:])
            sys.exit(1)
        time.sleep(0.2)
    t_ready = time.time() - t0

    p.stdin.write("GEN %d %s\n" % (nnew, ",".join(map(str, prompt))))
    p.stdin.flush()
    t1 = time.time()
    while not done and time.time() - t1 < 900:
        if p.poll() is not None:
            break
        time.sleep(0.2)
    t_gen = time.time() - t1
    try:
        p.stdin.close()
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass
    p.wait(timeout=30)

    log = open(err.name, encoding="utf-8", errors="replace").read()
    pool_used = "the expert pool mapped" in log
    hyb = re.findall(r"hybrid experts:.*?; ([\d.]+) s", log)
    gen = re.findall(r"(\d+) generated in ([\d.]+) ms \(([\d.]+) tok/s\)", log)
    return toks, t_ready, t_gen, pool_used, hyb, gen, log


print("=== A: the engine's own copy (no pool) ===", flush=True)
a, ta, ga, pa, ha, gena, loga = run(False)
print("tokens:", a)
print("ready %.1fs  gen %.1fs  pool_used=%s  place=%s  gen_log=%s" % (ta, ga, pa, ha, gena), flush=True)

print("=== B: the offline pool ===", flush=True)
b, tb, gb, pb, hb, genb, logb = run(True)
print("tokens:", b)
print("ready %.1fs  gen %.1fs  pool_used=%s  place=%s  gen_log=%s" % (tb, gb, pb, hb, genb), flush=True)

print()
print("B used the pool? %s (expected True)" % pb)
print("startup: copy %.1fs -> pool %.1fs  (saving %.1fs)" % (ta, tb, ta - tb))
print("token count A=%d B=%d" % (len(a), len(b)))
if a == b:
    print("RESULT: BITWISE IDENTICAL (%d tokens)" % len(a))
elif len(a) == len(b):
    diff = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    print("RESULT: DIFFER at %d positions, first=%s" % (len(diff), diff[:5]))
    print("  A:", a[:16])
    print("  B:", b[:16])
else:
    print("RESULT: LENGTH MISMATCH")
    print("  A:", a)
    print("  B:", b)
