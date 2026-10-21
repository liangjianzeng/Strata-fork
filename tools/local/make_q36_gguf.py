# Build a Strata-q36-friendly GGUF:
#  - routed experts (ffn_*_exps.weight, 3D): keep NVFP4 + .scale F32 (engine reads them; scale tail patched in engine)
#  - shared experts (ffn_*_shexp.weight, NVFP4): dequant -> Q8_0
#  - dense BF16 (except token_embd): -> Q8_0
#  - everything else: verbatim
from gguf import GGUFReader, GGUFWriter, GGUFValueType, GGMLQuantizationType
Q8 = GGMLQuantizationType.Q8_0
import numpy as np, shutil, os, time

SRC = r"D:\MyLLM\models\Ornith-1.5-35B-A3B-NVFP4-fix.gguf"
DST = r"D:\MyLLM\models\Ornith-1.5-35B-A3B-NVFP4-q36.gguf"
TMP = DST + ".tmp"

KV = np.array([0, 1, 2, 3, 4, 6, 8, 12, 0, -1, -2, -3, -4, -6, -8, -12], dtype=np.int8)

def ue4m3_to_f32(u):
    """ggml_ue4m3_to_fp32: returns E2M1*2 * scale, i.e. value = kvalues * this."""
    u = u.astype(np.uint8)
    exp = (u >> 3).astype(np.int32) & 0xF
    man = (u & 7).astype(np.int32)
    out = np.where(u == 0, 0.0, np.where(exp == 0,
                     np.ldexp(man.astype(np.float32), -9),
                     np.ldexp(1.0 + man / 8.0, exp - 7)))
    out = np.where(u == 0x7F, 0.0, out)
    return (out * 0.5).astype(np.float32)

def dequant_nvfp4_row(raw, n):
    """raw: bytes of a row (n elements, n%64==0). Returns float32[n] WITHOUT weight_scale_2."""
    nb = n // 64
    b = np.frombuffer(raw, dtype=np.uint8).reshape(nb, 36)
    scales = ue4m3_to_f32(b[:, :4])                    # [nb, 4]
    qs = b[:, 4:].reshape(nb, 4, 8)                    # 4 sub-blocks x 8 packed bytes
    lo = (qs & 0x0F).astype(np.int8)
    hi = (qs >> 4).astype(np.int8)
    vals = np.empty((nb, 4, 16), dtype=np.float32)
    vals[:, :, :8] = KV[lo]
    vals[:, :, 8:] = KV[hi]
    vals *= scales[:, :, None]
    return vals.reshape(nb * 64)

def quant_q8_0(x):
    """x float32 -> (d fp16 uint16, q int8[32-rounded]) in Q8_0 block layout."""
    nb = len(x) // 32
    xb = x[:nb * 32].reshape(nb, 32)
    amax = np.abs(xb).max(axis=1)
    d = (amax / 127.0).astype(np.float32)
    iden = np.where(d > 0, 1.0 / np.where(d > 0, d, 1), 0.0).astype(np.float32)
    q = np.clip(np.rint(xb * iden[:, None]), -127, 127).astype(np.int8)
    d16 = d.astype(np.float16).view(np.uint8).reshape(nb, 2)
    return np.concatenate([d16, q.view(np.uint8)], axis=1).tobytes(), nb

def bf16_to_f32(raw, n):
    u = np.frombuffer(raw, dtype=np.uint16)[:n].astype(np.uint32) << 16
    return u.view(np.float32)

t0 = time.time()
r = GGUFReader(SRC)
w = GGUFWriter(TMP, arch=None)
n_kv = 0
for name, f in r.fields.items():
    if name.startswith("GGUF."):
        continue
    try:
        w.add_key_value(name, f.contents(), f.types[0])
        n_kv += 1
    except Exception as e:
        print("kv fail:", name, e)
print("kv:", n_kv)

stats = {"nvfp4_keep": 0, "shexp_nvfp4": 0, "bf16_to_q8": 0, "keep": 0}
scale_cache = {}
for t in r.tensors:
    name, tt, shape = t.name, t.tensor_type.name, [int(d) for d in t.shape]
    data = t.data
    if tt == "NVFP4" and name.endswith("_exps.weight") and len(shape) == 3:
        stats["nvfp4_keep"] += 1                       # routed experts stay NVFP4
        w.add_tensor(name, data, raw_dtype=t.tensor_type)
    elif tt == "NVFP4" and name.endswith("_shexp.weight") and len(shape) == 2:
        n_in, n_out = shape                            # [2048, 512]
        n = n_in * n_out
        sc_name = name[:-7] + ".scale"  # strip ".weight"
        sc = scale_cache.get(sc_name)
        if sc is None:
            for t2 in r.tensors:
                if t2.name == sc_name:
                    sc = float(np.frombuffer(t2.data, dtype=np.float32)[0])
                    scale_cache[sc_name] = sc
                    break
        sc = 1.0 if sc is None else sc
        x = dequant_nvfp4_row(data, n_in * n_out) * np.float32(sc)
        blob, nb = quant_q8_0(x)
        rb = (shape[0] // 32) * 34
        arr = np.frombuffer(blob, dtype=np.uint8).reshape(shape[1], rb)
        w.add_tensor(name, arr, raw_shape=list(arr.shape), raw_dtype=Q8)
        stats["shexp_nvfp4"] += 1
    elif tt == "BF16" and name != "token_embd.weight":
        n = int(np.prod(shape))
        x = bf16_to_f32(data, n)
        blob, nb = quant_q8_0(x)
        rb = (shape[0] // 32) * 34
        rows = int(np.prod(shape[1:])) if len(shape) > 1 else 1
        arr = np.frombuffer(blob, dtype=np.uint8).reshape(rows, rb)
        w.add_tensor(name, arr, raw_shape=list(arr.shape), raw_dtype=Q8)
        stats["bf16_to_q8"] += 1
    else:
        stats["keep"] += 1
        w.add_tensor(name, data, raw_dtype=t.tensor_type)
print(stats)

w.write_header_to_file()
w.write_kv_data_to_file()
w.write_tensors_to_file()
w.close()
shutil.move(TMP, DST)
print("done %.1fs ->" % (time.time() - t0), DST, os.path.getsize(DST))
