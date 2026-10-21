# -*- coding: utf-8 -*-
"""Bitwise parity: the packed Q8_0 decode copies given back vs kept.

The engine can hand the packed Q8_0 decode copies back to the system (1.28 GiB
here) so that more expert slots fit in VRAM.  The decode then reads the GGUF
layout instead of the packed copy.  Upstream claims bitwise identical outputs,
a little slower.  This drives the engine's own --serve protocol with the same
prompt and compares the generated token ids.

Usage: packed_parity.py <slots> <n_new>
Prints the two token sequences and whether they match.
"""
import subprocess, sys, threading, time, os, re

EXE = r"D:\MyLLM\strata-build2\strata.exe"
MODEL = r"D:\MyLLM\models\Ornith-1.5-35B-A3B-NVFP4-q36.gguf"
CTX = 160000
CHUNK = 2048

slots = int(sys.argv[1]) if len(sys.argv) > 1 else 40
nnew = int(sys.argv[2]) if len(sys.argv) > 2 else 24

ids = [int(x) for x in open(r"D:\MyLLM\_tmp_code\realtok.txt").read().strip().split(",")]
prompt = (ids * (4000 // len(ids) + 1))[:4000]


def run(keep_packed):
    env = dict(os.environ)
    if keep_packed:
        env["STRATA_Q36_KEEP_PACKED"] = "1"
        env.pop("STRATA_Q36_GIVE_PACKED", None)
    else:
        env.pop("STRATA_Q36_KEEP_PACKED", None)
        env["STRATA_Q36_GIVE_PACKED"] = "1"   # force the give-back so both paths are compared
    tag = "keep" if keep_packed else "giveback"
    err = open(rf"D:\MyLLM\_tmp_code\parity_{tag}.log", "w", encoding="utf-8", errors="replace")
    p = subprocess.Popen(
        [EXE, "--serve", "--model", MODEL, "--max-context", str(CTX), "--kv", "q4_0",
         "--prefill", str(CHUNK), "--mtp", "0", "--batch", "2", "--expert-slots", str(slots)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True,
        bufsize=1, encoding="utf-8", env=env)

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
            print(open(err.name, encoding="utf-8", errors="replace").read()[-1200:])
            sys.exit(1)
        time.sleep(0.5)
    t_ready = time.time() - t0

    p.stdin.write("GEN %d %s\n" % (nnew, ",".join(map(str, prompt))))
    p.stdin.flush()
    t1 = time.time()
    while not done and time.time() - t1 < 600:
        if p.poll() is not None:
            break
        time.sleep(0.2)
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
    given = "given back" in log
    timings = re.findall(r"(\d+) generated in ([\d.]+) ms \(([\d.]+) tok/s\)", log)
    return toks, t_ready, given, timings, log


print("=== A: packed copies KEPT ===", flush=True)
a, ta, ga, tima, loga = run(True)
print("tokens:", a)
print("ready %.1fs  packed_given_back=%s  timings=%s" % (ta, ga, tima), flush=True)

print("=== B: packed copies GIVEN BACK ===", flush=True)
b, tb, gb, timb, logb = run(False)
print("tokens:", b)
print("ready %.1fs  packed_given_back=%s  timings=%s" % (tb, gb, timb), flush=True)

print()
print("B given back? %s (expected True)" % gb)
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
