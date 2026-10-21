# Strata 原生运行 Ornith-1.5-35B-A3B-NVFP4 —— 基线报告（2026-10-20）

## 结论

**Strata 0.1.45.3（q36 引擎）原生跑通 NVFP4 权重。**
短上下文的 prefill 约为 llama.cpp 路线的 2 倍；**decode 与 llama.cpp+DFlash 相当（不是「1.5 倍」）**——
后者靠 DFlash 草稿（mean len 3.5–3.75）把有效吞吐抬到 45.7–67 t/s，裸 forward 速率其实只有 ~20 t/s。

| 指标 | Strata 原生 (q36) | llama.cpp b11539（NVFP4，无草稿） | llama.cpp b11539（NVFP4 + DFlash 草稿） |
|---|---|---|---|
| prefill pp2048 | **1100–1282 t/s** | ~600 t/s | 437–620 t/s |
| decode tg128 @32K ctx（用户可见） | **47–55 t/s** | 35–40 t/s | 45.7–67.0 t/s |
| decode tg128 @96K ctx | ~43 t/s（推断，见下表） | — | 45.7–67.0 t/s |
| **decode tg128 @256K ctx（单路，q4_0 KV）** | **46.5–49.2 t/s**（48 槽） | ctx 封顶 96K | ctx 封顶 96K |
| **decode tg128 @双路 200K ctx（q4_0 KV）** | **60.5 t/s**（48 槽 + 归还 packed） | ctx 封顶 96K | ctx 封顶 96K |
| **decode tg128 @256K ctx（当前生产档，q8_0 KV）** | **36–43 t/s**（48 槽，视觉开） | ctx 封顶 96K | ctx 封顶 96K |
| decode 裸 forward（÷ mean len） | 47–55 t/s | 35–40 t/s | **~20 t/s**（3.53–3.75 tok/forward） |
| 上下文上限（16 GB 卡） | **单路 256K**（q8_0 2.92 GiB 或 q4_0 1.55 GiB）/ **双路 200K×2** | 96K（脚本实测上限） | 96K（脚本实测上限） |
| 192K prompt 正确性 | **3/3 埋点命中，零乱码** | 未测 | 未测 |

- **三列的主模型是同一份权重**：两个 llama.cpp 档跑的都是 `models/Ornith-1.5-35B-A3B-NVFP4.gguf`（23.85 GB，W4A8）。
  DFlash 是**投机解码的草稿模型**（`Ornith-1.5-35B-A3B-DFlash-q8_0.gguf`，402 MB，`--spec-type draft-dflash --spec-draft-n-max 4`），
  与主模型的量化格式正交 —— **「+DFlash」不等于换量化**，它只改 decode 路径（draft acceptance 0.40–0.85，mean len 2.59–3.75）。
- **decode 口径必须分两层看**：llama.cpp+DFlash 的 45.7–67 t/s 是「用户看到的 token 出字速度」，
  含草稿一次 forward 吐 2.59–3.75 个 token 的放大；除以 mean len 得到的**裸 forward 速率只有 ~20 t/s**，
  反而低于 Strata 的 47–55 t/s。两者在「用户可见速度」上打平，**在「每个 token 的算力成本」上 Strata 明显更省**。
- Strata 的 prefill 优势在长上下文更明显（llama.cpp 96K 封顶，Strata 256K）。
- 显存占用：dense+KV+缓冲 ≈ 4.34 GiB GPU + 专家池 4.35 GiB = **~8.7/16 GB**，余量 6.7 GiB。

## 专家驻留口径：**两条路线都不是「专家全装显存」**

> 上游 README 那句「NVFP4 needs about 80 GB of RAM to keep every expert in RAM」指的是上游 Flash-Next 家族的
> 63 GiB 专家规模，**不适用于本模型**（路由专家 17.67 GiB）。NVFP4 本身并不要求专家全部驻留显存。

| 路线 | 专家放在哪 | 机制 |
|---|---|---|
| **Strata q36** | **全部 17.67 GiB 在 host 内存**（默认 `cudaHostAlloc` 锁页；`--expert-pool` 改为文件映射），每层只让 21–63 / 256 个进 VRAM（1.45–4.35 GiB，生产档 48 个 = 3.31 GiB） | 未驻留的专家走 16 线程 CPU pool 算，prefill 的大 chunk 经 PCIe 流式送入（1.59 GiB staging） |
| **llama.cpp b11539** | 前 `--n-cpu-moe 18` 层的 MoE 权重留在 **CPU**，其余进 VRAM | CPU 后端有 NVFP4 的 `ggml_vec_dot_nvfp4_q8_0`（`ggml-cpu.c` 的 `[GGML_TYPE_NVFP4]` traits），CPU 侧能直接算 NVFP4 专家；另有 `--moe-cache-mib` 给 CPU 上的专家做 GPU cache |

实测（`8090_ornith_nvfp4_cpu18.bat`）：

```
strata-q36: hybrid experts: 63 of 256 per layer in VRAM (4.35 GiB), all 17.67 GiB in pinned host memory,
  the rest of a batch of <= 128 rows on 16 CPU threads, bigger prompt chunks streamed (1.59 GiB staging)
strata.exe  常驻工作集 17.56 GiB  ← 17.67 GiB 锁页专家池
```

## 模型格式链

```
HF NVFP4 safetensors (W4A8, 22GB, ModelOpt)
  → convert_hf_to_gguf.py (NVFP4 repack: 专家 NVFP4 + dense BF16 + .scale F32)
  → fix_router_shape.py (41 个 ffn_gate_inp_shexp 退化 2D [2048,1] → 1D [2048])
  → make_q36_gguf.py (Strata q36 定制，20.4GB)：
      · 120 个 routed expert **张量** (blk.0-39 × gate/up/down，每张量含 256 个专家) 保留 NVFP4
        （另 120 个同名 F32 `.scale` 保留为 F32，MTP 块的 3 个 routed expert 是 Q8_0）
      · 123 个 shexp NVFP4 → Q8_0（dequant+requant，含 weight_scale_2）
      · 221 个 dense BF16 → Q8_0（含 MTP 层 BF16 专家、attention 投影、SSM 投影）
      · 其余原样（router F32、norms、token_embd BF16、output.weight NVFP4+.scale）

> 转换链产出 994 个张量，类型直方图实测：`551 F32 + 321 Q8_0 + 121 type 40 (NVFP4) + 1 BF16`。
> ⇒ **type 40 的 121 个 = 120 个 routed expert 张量 + 1 个 `output.weight`**。
> （上游那份未做 q36 定制，`Ornith-1.5-35B-A3B-NVFP4.gguf` 23.85 GB 的 type 40 是 241 个，
> 因为它的 shexp 也是 NVFP4 未转 Q8_0 —— **不是同一份文件，别混用**。）
```

## 引擎 patch（全部已编译进 strata-q36.exe / strata.exe）

1. **family_layout.hpp**：① 未表列 `.scale` 张量放行；② `_exps.weight` 允许 Q8_0 重打包后的 2D 展平 `[d0, d1*d2]`（数据线性布局与 3D 完全等价，引擎按几何参数而非张量 shape 取专家）。
2. **q36_engine.cu 专家尾包**：NVFP4 专家加载时读 `ffn_{gate,up,down}_exps.scale`（F32, weight_scale_2 per expert），填入 blob 尾包 `{s_gate, s_up, s_down, 0}`。
3. **q36_engine.cu lm_head**：ModelOpt 把 output.weight 也量化成 NVFP4（MMVQ 不支持）→ 加载时 host 一次性 dequant+requant 成 Q8_0（515.3 MiB，ws=0.000111171，约 1 s），完全复用现有 Q8_0 MMVQ 路径。
4. **q36_engine.cu KV 存储档**（`--kv`）：`fp16`（F32 池）/ `int8` / `q4_0` / `k8v4` / **`q8_0`**（ggml 布局，32 codes + 每 32 值一个 fp16 scale）。

## 修复的引擎缺陷（2026-10-21）

### ① F32 KV 缓存少分配 4 倍 → 长 prompt 乱码（根因）

`kv_half_bytes(kvm=0)` 没有 F32 分支，落进 int8 布局返回 **264 B/cell**，而内核按 F32 **1024 B/cell** 读写 → K/V 池只有应有内存的 1/4，**第 2 个及之后的 KV 头的写全部越界**。

ctx=32768 时 `kc[3] + (1*ctx+4096)*HD` **逐字节等于 S[4]**（第 4 层的 GDN 状态）→ pos ≥ 4096 起，K(kvh=1) 的写直接砸进 GDN 状态 → 第 4 层 router logits 粗大损坏（l2 ≈ 39）→ **输出乱码**。

定位方法（可复现）：层内阶段二分（qkv/z/α/β/conv 输出全同，`gdn_y` 差 l2=5.2）→ 状态探针（chunk 2 入口 S[4] 已 differ l2=23，逐层入口显示 layer 0-3 干净、layer 4 入口被污染）→ 地址打印实锤 + 污染值与 K(kvh=1, cell 4096) **bitwise 相等**。

修复：`kv_half_bytes` 为 kvm==0 返回 `hd*4`（一行）。int8 / q4_0 / k8v4 / q8_0 的尺寸本就与内核 cell 步长一致，未受影响。

> 副作用（正确行为）：F32 路径显存需求 ×4 → **F32 @256K ctx 需 ~11.0 GiB，16GB 卡放不下**；大 ctx 必须用量化档。超配从「静默写坏内存」变成「启动即报错」。

### ② NVFP4 尾包 scale 丢失 → 路由专家量级全错

MMQ 乘积只带了 per-block scale，blob 尾包的 per-expert `weight_scale_2 {s_gate, s_up, s_down}` 从未施加 → 每个路由专家的 gate / up / down 行量级错误（尾包是零填充，所以表现像纯垃圾而不是轻微漂移）。

修复：每层收集 `d_tails`，在 SwiGLU 前对 gate/up 行施加（`scale_gu_rows`）、在 combine 前对 down 乘积施加（`scale_down_rows`）。

### ③ int8 布局的会话拷贝错误（静默状态损坏）

`copy_session()` 用单次 `cudaMemcpy2D` 按「每 head 的 cell 连续」拷贝，而 int8 布局是**平面的**（所有 cell 的 codes 区，然后所有 cell 的 scales 区）→ 批处理路径下「一个会话接续另一个会话」拷错字节。改为 codes / scales 两段拷贝（F32 / q4_0 / q8_0 每 cell 连续，不受影响）。

## KV 存储档位

| `--kv` | 格式 | B/half（单 KV 头的 K 或 V） | 每 token 全模型 | 256K ctx KV 占用 | 备注 |
|---|---|---|---|---|---|
| `fp16` | F32 池 | 1024 | 44 KiB | 11.0 GiB | 16GB 卡放不下；**修复后才是真实值** |
| **`q8_0`** | ggml q8_0（32 codes + 1 fp16 scale / 32 值） | **272** | **11.7 KiB** | **2.92 GiB** | 推荐档（生产在用） |
| `int8` | codes + 1 fp16 scale / 64 值 | 264 | 11.3 KiB | 2.84 GiB | 粒度比 q8_0 粗一倍 |
| `k8v4` | K int8 / V q4_0 | 混合（264/144） | 8.0 KiB | — | — |
| `q4_0` | ggml q4_0（18 B / 32 值） | 144 | 6.2 KiB | 1.55 GiB | 精度最低 |

> 「每 token 全模型」= 11 个带 KV 的层（10 全注意力 + MTP）× 2 个 KV 头 × (K+V) × B/half。
> 引擎实测 int8 @ ctx 262144 的 KV 占用 **2.84 GiB**、剩余显存 2.97 GiB；q8_0 剩余 2.89 GiB（吻合）。

## 长上下文验收（2026-10-21，生产服务真实路径）

服务：`serve.server --engine strata --config strata-ornith-nvfp4-8090.json --port 8090`，`--max-context 262144 --kv q8_0 --mtp 2`，VRAM 14654 / 16311 MiB。

> 下表是 **q8_0 KV 档**的结果。**q4_0 + 48 slots 档的复测见下一节**（已 ALL CLEAN）。

| 测试 | prompt_tokens | 耗时 | 结果 |
|---|---|---|---|
| 短问答 3 题 | ~20 | 1.5–8.4 s | 中文正常、翻译正常、finish=stop |
| 针刺（秘密在开头，末尾提问） | 34,137 | 34 s | **HIT** |
| 长上下文算术（17×23） | 22,721 | 24 s | **391** ✓ |
| 同 prompt 连发两次 | ~20 | — | **逐字一致**（确定性成立） |
| 埋点阶梯 ~25K | 28,539 | 32 s | 3/3 命中，零乱码 |
| 埋点阶梯 ~50K | 57,638 | 69 s | 3/3 命中，零乱码 |
| 埋点阶梯 ~100K | 116,437 | 206 s | 3/3 命中，零乱码 |
| **埋点阶梯 ~180K** | **208,837** | 606 s | **3/3 命中，零乱码** |

### q4_0 KV + 48 slots 单路 256K 档复测（2026-10-21 晚）

`--max-context 262144 --kv q4_0 --expert-slots 48 --mtp 2`，峰值显存 15218 MiB。
q4_0 是**精度最低的 KV 档**（每 32 值一个 fp16 scale）。

| 测试 | prompt_tokens | 耗时 | 结果 |
|---|---|---|---|
| 短问答 3 题 | ~20 | 1.5–8.4 s | 中文正常、翻译正常、`1234*5678=7006652` 精确 |
| 埋点阶梯 ~25K | 28,539 | 30 s | **3/3 命中，零乱码**，finish=stop |
| 埋点阶梯 ~50K | 57,638 | 69 s | **3/3 命中，零乱码** |
| 埋点阶梯 ~100K | 116,437 | 202 s | **3/3 命中，零乱码** |
| **埋点阶梯 ~180K** | **208,837** | **600 s** | **3/3 命中，零乱码**，三处埋点逐字正确 |
| 长请求后短请求 ×3 | ~20 | 4.2–4.6 s | **43.2 / 48.1 / 48.1 t/s**，无退化 ⇒ 长请求不污染后续会话 |

- ✅ **ALL CLEAN**：五档全部 `finish=stop`、三处埋点逐字命中、零乱码（替换字符 / 斜杠失控 / 4-gram 重复 / 单字沙拉均未触发）。
- **decode 速率**：短请求 43–48 t/s（与 bench 档 46.5–49.2 一致）；**208K 满 ctx 时降到 13.1 t/s**
  （旧 q8_0 档同条件 10.3 t/s，**+27%**）—— 这是长上下文读 KV 带宽的固有开销，不是静默退化
  （长请求后紧接的短请求仍是 43–48 t/s，可据此与「变体③静默退化」区分）。
- ⚠️ 取结果要合并 `content` + `reasoning_content`（见下条）。

- ⚠️ `finish=length` + `content` 为空 ≠ 故障：模型有思考链，答案在 `reasoning_content` 里，max_tokens 太小会被思考链吃满 —— **取结果要合并两个字段**。
- ⚠️ HTTP `/v1/chat/completions` 无状态：多轮「不记得上一句」是 API 语义（历史由客户端带回），非引擎缺陷。

### q8_0 KV + 48 slots + 视觉（当前生产档，2026-10-21 18:00 起）

```
--max-context 262144  --kv q8_0  --expert-slots 48  --prefill auto  --mtp 2  --vision
```

单槽 256K（**删掉 `--batch 2` 回单槽**）。KV 从 q4_0 的 1.55 GiB 涨到 **2.92 GiB**，48 专家槽 3.31 GiB 不变，
CPU 加载 mmproj 所以视觉不吃显存 —— 三者仍能共存。

**性能（短请求，含 thinking budget）**：decode **36.2 / 37.0 / 39.3 / 36.0 / 39.1 / 43.3 t/s**（6 轮）。
比 q4_0 档的 46.5–49.2 t/s **降 20–25%**，与 KV 每 token 从 6.2 KiB 涨到 11.7 KiB 的预期一致。

**视觉：曾因缺 CUDA 运行库起不来（已修）**

| 现象 | 根因 | 修法 |
|---|---|---|
| `RuntimeError: the vision encoder did not start:`（**冒号后为空**） | config 无 `"log"` 键 ⇒ 编码器 stderr 进 DEVNULL，真实原因被吞 | 手动跑 `strata-vision.exe` 才看到真因 |
| `error while loading shared libraries: cublas64_13.dll` | 该 DLL 不在 System32、也不在 `strata-vision.exe` / `strata-build2\` 旁边 | 启动前 `set "PATH=D:\MyLLM\llama.cpp;%PATH%"` |

- ⚠️ **mmproj 走 CPU ≠ exe 不依赖 CUDA**：视觉编码器启动时仍要链接 `cublas64_13.dll`。手头唯一干净副本在
  `D:\MyLLM\llama.cpp\`（CUDA 13.4 自包含构建）—— **那个目录不能删**，删了视觉立刻挂。
- CPU 模式下手动运行会打两行**正常**日志，别误判为故障：
  `ggml_cuda_init: failed to initialize CUDA`（没有 `--gpu` 就这样）、
  `load_hparams: Qwen-VL models require at minimum 1024 image tokens`（当前默认 2048）。
- `vision_env()` 只认 `cuda_device`，**不支持任意自定义 env** ⇒ 只能从启动脚本注入 PATH。
- 手动验证：注入 PATH 后 **6.2 s 就绪**，输出 `READY 2048` + `on the CPU, 16 threads, no warm-up`。
- 配置段写法：`"vision": {"exe", "mmproj", "model", "gpu": false, "threads": 16}`；`gpu:false` = mmproj 落 host RAM。
  serve 层逻辑（`server.py:2878`）：**有** vision 段但 args 缺 `--vision` 会自动补；**没有** vision 段则编码器根本不启动。
- ⚠️ **该模型输出走 `reasoning_content` 而非 `content`**（thinking 风格）。`max_tokens` 用尽时
  `content = null` + `finish=length`，脚本直接读 `content` 会 TypeError；**必须带 `reasoning_budget_tokens`**，
  否则日志刷 `the reply reached max tokens while still thinking`。


## 运行时行为（默认参数）

```
753 tensors (layout checked), 4.34 GiB on the GPU in 4.9 s
prompt experts on MMQ (int8)
context 32768 cells, prompt chunk 2048, MTP block loaded, 6.70 GiB VRAM free
hybrid experts: 63 of 256 per layer in VRAM (4.35 GiB), all 17.67 GiB pinned host,
  batch <=128 rows on 16 CPU threads, prompt chunks streamed (1.59 GiB staging)
```

- 加载 + 专家池预取约 50 s；warmup 1.8 s。
- 专家命中路径自适应（first-use admission）；bench 期间 39.5% batch entries 走 GPU、60.5% 走 CPU pool（16 线程）。

### ctx 越大 → 专家驻留越少 → decode 越慢（256K 生产档的真实代价）

| ctx / KV 档 | VRAM 余量 | 专家驻留（每层 /256） | GPU 命中率 | 实测 decode（用户可见） |
|---|---|---|---|---|
| 32K / int8 | 6.74 GiB | 63（4.35 GiB） | 39.5% | 47–55 t/s（bench 档） |
| 64K / q8_0 | 6.21 GiB | 55（3.80 GiB） | — | ~43 t/s |
| 256K / q8_0 + 21 slots（更早的生产档） | 2.89 GiB | 7（0.48 GiB） | **8.1%** | 29–33 t/s（长 prompt 10.3） |
| 256K / q4_0 + 48 slots（单路档） | 3.31 GiB | **48（3.31 GiB）** | **24.5%** | **46.5–49.2 t/s** |
| 双路 200K / q4_0 + 48 slots | 余 767 MiB | **48（3.31 GiB）** | **19.9%** | **60.5 t/s** |
| **256K / q8_0 + 48 slots + 视觉（当前生产档）** | KV 2.92 GiB | **48（3.31 GiB）** | — | **36–43 t/s** |

- 这是**上下文长度、KV 精度、专家驻留、decode 速度的四方交换**：要 256K 就得压 KV 档位，
  压出来的显存换成专家驻留，命中率上去后 decode 才回来。
- ⚠️ llama.cpp+DFlash 的 45.7–67 t/s 是在 **96K ctx** 下测的，与本表 256K 档**不构成同档对比**。

### 双槽（`--batch 2`）并发与「1 GiB 显存留量」之谜（2026-10-21 晚）

`--batch N` 让引擎同时解码 N 路会话，**每路一份完整 KV，不共享不分片**
（`q36_engine.cu:1833` 的 `per = kv_half_bytes(...) × n_head_kv × ctx × n_attn`，`:1842` 硬检查
`per × n_sess + 1 GiB > fr0` 直接 `exit(1)`）。与 llama.cpp 的 `--parallel`（`kv_unified` 或槽内分片）语义完全不同。

**双槽 200K 的槽数上限**（每槽 70.7 MiB，`need > fr` 是 `cudaMemGetInfo` 实测空闲，非保守估计）：

| 配置 | 结果 | 引擎原文 |
|---|---|---|
| 双槽 200K + 48 槽（归还 packed 前） | **崩** | `5153 MiB needed, 4640 MiB free` |
| 双槽 200K + 41 槽 | **崩** | `4658 MiB needed, 4640 MiB free`（差 18 MiB） |
| 双槽 200K + 40 槽 | 过（余 53 MiB） | 40 槽曾是硬上限 |
| **双槽 200K + 48 槽（归还 packed 后）** | **过（余 767 MiB）** | 60.5 t/s，GPU 命中率 19.9% |

#### 那 1 GiB 到底被谁占了

**不是留给桌面的。** 空载实测独显占用 **0 MiB**（16311 total / 0 used）—— 桌面合成与唯一输出都在核显。

16GB 卡的完整账本（双槽 200K / 48 槽，引擎自报）：

| 项 | 大小 | 说明 |
|---|---|---|
| 权重 dense | 4.34 GiB | 路由专家留在 host |
| **packed Q8_0 decode 副本** | **1.28 GiB** | **混合专家模式下白占**（见下） |
| KV 两路 × 1.27 GiB | 2.53 GiB | q4_0，6.50 KiB/token（引擎实测反解） |
| 专家槽 48 × 70.7 MiB | 3.31 GiB | 每层 48/256 驻留 |
| prompt chunk staging | 1.59 GiB | 2 × (max_layer + tail) |
| 引擎固定缓冲（att scores / CUDA ctx） | ~2.87 GiB | 真实工作缓冲 |
| `fr` = 分配专家池前的实测空闲 | 4.53 GiB | 与 40 槽的 `need` 4587 MiB 对上 |

引擎源码里三处「留量」，只有一处是真给别人的：

1. `reserve = 768 MiB`（`:3214`）—— **只在自动算槽路径生效**，显式 `--expert-slots` 时不参与计算，
   它的用途是防驱动抖动，不是给别的程序用。
2. `:1842` 多会话硬检查的 **1 GiB 安全垫**（`per × n_sess + 1 GiB > fr0`）—— 双槽专属。
3. `:3300` 的 256/512 MiB 缓冲余量 —— 真实工作缓冲，不能动。

#### 真正的浪费：packed Q8_0 decode 副本 1.28 GiB

混合专家模式把全部路由专家放在锁页 host 内存、只让热专家进 VRAM 槽，
**这些 dense decode 副本在 hybrid 路径上根本没人读**。引擎本来就有「归还副本」的代码
（`:3227-3241`），但门禁是 `slots <= 0`（仅自动算槽模式）⇒ 显式指定槽数时这 1.28 GiB 一直白占。

**改动**（commit `4ed69349`）：显式 `--expert-slots N` 时，若「保留副本装不下、归还后装得下」，
就归还副本 —— 空闲从 4640 升到 5920 MiB，48 槽直接过。

**代价与验证**：decode 改读 GGUF 布局（上游文档：bitwise 相同、略慢）。实测同一 prompt 走
`--serve` 协议、48 槽 160K 双槽，**保留 vs 归还产出完全相同的 24 个 token id**，
且归还组不更慢（55.1 vs 53.3 t/s）⇒ 多出的 8 个专家槽收益 > 副本损失。

| 档位 | decode | GPU 命中率 |
|---|---|---|
| 双槽 200K + 40 槽（保留副本，旧上限） | 56.5 t/s | — |
| **双槽 200K + 48 槽（归还副本，q4_0 档）** | **60.5 t/s** | **19.9%** |

环境变量：`STRATA_Q36_KEEP_PACKED=1` 保留副本、拒绝交易；`STRATA_Q36_GIVE_PACKED=1` 强制归还（对比两条 decode 路径用）。

#### 双槽 200K + 48 槽的长上下文门禁（2026-10-21 16:00，q4_0 档）

上生产前的最后一关。档位脚本 `_tmp_code/ctxladder2.py`，打生产服务真实 HTTP 路径，
每档 3 处埋点（开头 / 中间 / 结尾）必须逐字命中。

| 档位 | 耗时 | prompt_tokens | 埋点命中 | finish |
|---|---|---|---|---|
| ~25K | 49 s | 28,539 | **3/3** | stop |
| ~50K | 70 s | 57,638 | **3/3** | stop |
| ~100K | 203 s | 116,437 | **3/3** | stop |
| **~180K** | 551 s | **192,037** | **3/3** | stop |

零乱码、零 `/` 死循环、零 token 沙拉，四档答案一致（紫罗兰色的风筝 / 第零号齿轮 / 青铜色的怀表）。

> ⚠️ 档位必须按 ctx 调：双槽后每路 ctx 从 256K 降到 204.8K，5000 行 = 208,837 token 会被引擎
> **明确 400 拒绝**（`prompt leaves no room to answer in the context`）—— **这不是 bug，是引擎不截断**。
> 已把最后一档从 5000 行改为 4600 行。

#### ⚠️ 术语澄清：Strata 语境下有两个「pack」，不是一回事

「packed」这个词在 Strata 里有**两个完全不同的所指**，混起来会得出错误结论
（比如「Qwen3.5-35B 官方没有 NVFP4 pack，所以专家卸载机制用不上」——这个判断对 pack 而言正确，但对我们这份模型**不构成问题**，见下）。

| | **A · 专家 pack** | **B · Q8_0 dense decode 副本** |
|---|---|---|
| 是什么 | **121 个 `type 40`（NVFP4）张量**：`blk.N.ffn_{gate,up,down}_exps.weight`（40 层 × 3）+ `output.weight` | **157 个**满足 packed 形状门槛的 Q8_0 张量的第二份拷贝：`attn_qkv`(n_out=8192, 30 个)、`attn_q`/`attn_output`(11 个)、`attn_gate`(4096, 30 个)、`ffn_down_shexp`(2048, 41 个)、`ssm_out`(30 个)、`nextn.eh_proj` 与 MTP 块 3 个专家等 |
| 干什么用 | **专家卸载机制的前提** —— 专家能按层拆开搬进 VRAM 槽，未驻留的走 CPU pool 补算 | 只服务 attention 与 shared expert 的 decode，走 `q8_packed_mmvq()` 快速路径 |
| 显存 | 就是路由专家本体的 17.67 GiB（锁页 host 内存 + 每层驻留槽） | **额外 1.28 GiB**（GGUF 原版已另有一份在显存） |
| 官方 35B 有吗 | **没有**。Strata 官方只给 **Qwen3.8-Flash-Next** 出了 NVFP4 pack；Qwen3.5-35B 无 | 不涉及模型版本，是引擎特性 |

**结论：官方没有 35B 的 NVFP4 pack ⇒ 「packed NVFP4」这个说法对本模型不成立；
但这不影响专家卸载机制 —— 因为我们手里本来就有 pack 形态的权重。**

我们这份 `Ornith-1.5-35B-A3B-NVFP4-q36.gguf`（21.9 GB）**不是官方产物**，是从
`models/ornith-nvfp4/`（HF safetensors + `hf_quant_config.json`，ModelOpt W4A8）**自行转换**出来的：
`convert_hf_to_gguf.py`（NVFP4 repack）→ `fix_router_shape.py` → `make_q36_gguf.py`（Strata q36 定制）。
转换链产出的就是「专家 NVFP4 + dense Q8_0」两段式布局，**A 种 pack 形态天然具备**。

> 另有 `Ornith-1.5-35B-A3B-NVFP4.gguf`（23.85 GB，10-20）是另一份文件，不是生产档用的那份。
> **两个 GGUF 中间产物都能从 `models/ornith-nvfp4/`（21.84 GiB safetensors，ModelScope 下载，
> 脚本 `models/ornith-nvfp4/dl.sh`）重建** —— 4 分钟重转第一个，84 秒重建专家池。
> ⚠️ `models/ornith-nvfp4/` **绝不能删**：它既是转换链源头，又是当前 8090 配置的 `"tokenizer"` 目录。
> ⚠️ 10-20 曾删掉 23.8 GB 的 `-fix.gguf` 中间产物而**磁盘空闲没有回升** ⇒ 真要清理前，
> 先拿一个小文件验证删除确实释放了空间，别删完 40 G 才发现还在。

GGUF 张量类型直方图（自写解析器，994 个张量）：
`551 F32 + 321 Q8_0 + 121 type 40 (NVFP4) + 1 BF16`。
其中 157 个 Q8_0 满足 packed 形状门槛（`n_in % 32 == 0 && n_out >= 2048`，`native_mmvq.cu:2048`），
**绝大多数是 dense 张量**（attention 投影 + shared expert + SSM 投影），只有 3 个是 MTP 块的
Q8_0 专家张量 ⇒ 引擎建出来的 B 副本**不覆盖任何一个 NVFP4 路由专家**，
**专家卸载机制完全没被它碰到**（40 主层的 120 个 routed expert 张量是 type 40，走 MMQ 路径）。

**B 副本为何会被建**：`q36_engine.cu:4238` 一句 `setenv("STRATA_Q8_PACKED", "1", 0)`
把它**默认强制打开**（`native_q8_0_packed_enabled()` 返回 `enabled || q8_sm60_on()`），
`q36_engine.cu:1315` 据此建副本，`native_mmvq.cu:2053` 在 registry 非空时走 packed 路径。
**这 1.28 GiB 与我们改动前的专家卸载机制完全无关** —— 17.67 GiB 路由专家仍全在锁页 host 内存，
每层 48/256 进显存槽，命中率 19.9%–24.5% 未受任何影响。

#### ⚠️ 判显存余量只能看 Task Manager，不能看 nvidia-smi

WDDM 下 `nvidia-smi` 只报物理 VRAM 且饱和后失真：生产跑满时它报 **4068 MiB used / 11983 free**，
而 Task Manager 同时显示**专用 15.0/16.0 GB** + **共享 17.9/31.4 GB**。共享那栏是
17.67 GiB 锁页专家池经 PCIe 被 GPU 访问（不是溢出）。

**共享显存兜底不免费**：同一配置（40 槽 / 200K）专家池放置耗时 **独占 45.7 s vs 与生产并发 96.6 s（慢 2.1 倍）**，
且**探针会把生产引擎挤到 `exit 1`**（服务层 HTTP 已 ready，但引擎懒启动失败 ⇒ 请求返回 503）。
⇒ **探针与生产服务必须互斥，跑探针前先停生产。**

### 专家扩容实测（2026-10-21，256K ctx，`--bench-pp 2048 --bench-tg 128`）

在 16GB 卡上，decode 的瓶颈不是算力而是**每 token 要从锁页 host 内存搬多少专家权重**。
两个杠杆：**`--kv` 降档**（省显存给专家池）+ **`--expert-slots N`**（显式指定每层驻留数）。

| 档位 | 驻留 | GPU 命中率 | tg128（三轮） | CPU pool |
|---|---|---|---|---|
| q8_0 + 21（自动，旧生产） | 1.45 GiB | 8.1% | 41.6 / 43.7 / 45.1 | 4090 ms |
| q8_0 + 30 | 2.07 GiB | 11.1% | 42.5 / 45.5 / 47.5 | 3772 ms |
| q4_0 + 41（自动） | 2.83 GiB | 20.0% | 43.1 / 48.2 / 48.5 | 3552 ms |
| **q4_0 + 48（选定）** | **3.31 GiB** | **24.5%**（两轮一致） | 48.2 / 45.9 / 47.5 · 46.8 / 46.5 / 49.2 | 3406 / 3364 ms |
| q4_0 + 50（引擎上限） | 3.45 GiB | 22.0% | 45.2 / 50.0 / 51.0 · 45.2 / 48.8 / 48.4 | 3222 / 3398 / 3436 ms |

- **相对基线：GPU 命中率 8.1% → 24.5%（3 倍），tg128 41.6–45.1 → 46.5–49.2（约 +10%）**，
  CPU pool 时间 4090 → 3364 ms（搬运量下降 18%）。**扩容确实有效，且是当前收益最大的一档调优。**
- **选 48 而不是上限 50**：50 档 tg 略高但**命中率反降 2.5pt**（slot replacement 1858 vs 1815 次挤占有效驻留）
  ⇒ 驻留数不是越多越好，超过某个点后自适应准入的抖动开始吃掉边际收益。48 是命中率峰值点。
- **引擎上限的来源**（`q36_engine.cu`）：`need > fr` 硬性检查用的是**分配专家池之前**的 `fr = 5332 MiB`，
  且没有动用 768 MiB 的 `reserve`。实测峰值显存 **15384 MiB（15.03 GiB）**、剩 927 MiB
  ⇒ **理论上还能再塞约 10 slots**，引擎是被自己的保守核算卡住的，不是物理装不下。
- ❌ `STRATA_Q36_NO_MTP_CPU=1` 反向变差（命中率 20.5% vs 24.5%），已放弃。

### 与 llama.cpp+DFlash 的公平对比（同 96K ctx）

| | Strata q36 @96K | llama.cpp b11539 + DFlash @96K |
|---|---|---|
| 用户可见 decode | ~43 t/s | **45.7–67.0 t/s**（胜） |
| 裸 forward 速率 | **~43 t/s**（胜） | ~20 t/s（71.03 ÷ 3.53） |
| 每 token 算力成本 | **低**（1 forward / token） | 高（1 forward / 2.59–3.75 token） |
| prefill | **1100–1282 t/s**（胜） | 437–620 t/s |
| 上下文上限 | **256K**（胜） | 96K |
| 208K 长上下文正确性 | **3/3 命中，零乱码** | 未测 |

**结论**：用户可见速度上 llama.cpp+DFlash 略胜（草稿的 token 放大效应），但那是**用更多算力换来的**；
单 token 的 forward 成本 Strata 明显更低。真正的差距在 **prefill（2 倍）与上下文上限（2.7 倍）**，
这两项在长文档/长对话场景里是决定性的。

### 若要再往上走（未采用，供后续参考）

> ⚠️ 本节是**单路 256K + q4_0 KV** 语境（当时的档位）。现生产档为**单路 256K + q8_0 KV + 视觉**，
> KV 占用 2.92 GiB（q4_0 的 1.88 倍），下面的旋钮需按此重新核算。

专家池已经吃掉 3.31 GiB，256K 的 KV 也已到位。剩下的旋钮：

| 手段 | 效果 | 代价 |
|---|---|---|
| 改 `need > fr` 核算让其动用 768 MiB reserve | 理论上可再放 ~10 slots（≈56） | 改引擎显存核算，需重编译 + 重跑 20/20 回归 |
| `--kv k8v4`（8.0 KiB/token，比 q8_0 省 ~1.1 GiB） | 多 ~15 slots，或退回双路 256K×2 | KV 精度档更激进，尚未做长上下文对拍 |
| `--max-context` 降到 192K | 专家驻留回 55+/256，decode 43+ | 放弃 256K 能力 |
| 加 `--batch 2` 做双路并发 | q4_0 档实测 60.5 t/s | KV ×2 ⇒ 必须降 KV 精度或 ctx（200K） |

> **当前生产档** `strata-ornith-nvfp4-8090.json`
> = `--max-context 262144 --kv q8_0 --expert-slots 48 --prefill auto --mtp 2 --vision`
> （**单路 256K + q8_0 KV + 48 专家槽 + 视觉走 CPU**；`start_strata_8090.bat` 同步）。
> 视觉段 `gpu: false` ⇒ BF16 mmproj 899 MB 落 host RAM，**不占显存**，所以 48 槽得以保住。
> 实测 decode 36–43 t/s，比 q4_0 档低 20–25%（换来更高的 KV 精度）。
> ⚠️ 起视觉前启动脚本必须注入 CUDA 13 运行库目录到 `PATH`（`cublas64_13.dll`）—— 详见上文视觉小节。
> 长上下文 4 档阶梯（28.5K / 57.6K / 116.4K / 192.0K）在同档位下全部 3/3 命中、零乱码。

## 基准明细

| 配置 | bench 0 | bench 1 | bench 2 |
|---|---|---|---|
| 默认 | pp 1115.3 / tg 48.8 | pp 1275.4 / tg 47.9 | pp 1281.4 / tg 50.0 |
| --mtp 2 | pp 1064.1 / tg 48.4 | pp 1217.6 / tg 49.6 | pp 1226.4 / tg 47.0 |
| --expert-vram 6 | pp 1113.6 / tg 53.6 | pp 1275.2 / tg 53.8 | pp 1281.6 / tg 55.3 |
| --expert-vram 6.4 --max-context 8192 | pp 1102.1 / tg 46.6 | pp 1275.9 / tg 51.0 | pp 1278.7 / tg 47.9 |

- `--mtp 2` 对 bench tg 无提升（瓶颈在专家池 CPU 摊薄，不在 draft）。
- `--expert-vram 6`：CPU pool 时间 2954→2237 ms，tg +8%；驻留数仍 63/256（自适应决定）。
- `--expert-vram 6.4 + 8K ctx`：驻留 69/256 但 tg 无稳定收益。

## 遗留事项

- ~~数值对拍（Strata vs llama.cpp 输出）未做~~ → **已做**（2026-10-21）：用 llama.cpp b11539 起独立真值
  probe（同 token 数组直喂 `/completion`，需 `return_tokens: true`），确认 4137 内的判据全部有效
  （争议位 logit gap 7.7–10.7）→ 由此定位到 F32 KV 分配缺陷。修复后跨 4096 边界的 5 个用例
  **20/20 复现文档自身续写**；30K token prompt 上 F32 / int8 / q4_0 / k8v4 / q8_0 **逐 token 相同**。
  lm_head 仍走 NVFP4→Q8_0 重量化，与原始 NVFP4 有轻微量化损失（与 shexp 同级）。
- 磁盘：中间产物 `Ornith-1.5-35B-A3B-NVFP4-fix.gguf`（23.8GB）已删除；HF 原始权重完整保留于 `D:\MyLLM\models\ornith-nvfp4\`（22GB），可随时重建。注意：D 盘删除 fix.gguf 后空闲空间未回升（21.7GiB，疑沙箱软删除暂存），重建文件前需先核实。
- 相关文件：`D:\MyLLM\_tmp_code\make_q36_gguf.py`、`fix_router_shape.py`；patch 源码 `strata-src/src/qwen35/q36_engine.cu`、`strata-src/include/strata/core/family_layout.hpp`；编译 `build_strata2.ps1`/`resume_strata2.ps1` → `strata-build2\strata-q36.exe`。
- 调试脚本留存 `D:\MyLLM\_tmp_code\`（`midcmp.py`/`lycmp.py`/`l3stage.py`/`kvmatch.py` 复现定位链，
  `reg.py`/`kvreg.py`/`kvq8.py`/`kvlong2.py` 回归，`qcheck.py`/`ctxladder2.py` 长上下文验收）。


---

## 离线专家池 `--expert-pool`（2026-10-21 16:00）

### 动机

hybrid 模式下引擎把**全部路由专家**以 native blob `[gate | up | down | tail]` 的形式放进 host 内存，
每次启动都重做一遍：从 mmap 的 GGUF 里逐专家 memcpy 进一块 `cudaHostAlloc` 的锁页 arena。
- **拷贝成本 = 45.7 s**（与生产并发时 96.6 s）
- **锁页内存 17.67 GiB**，叠在 GGUF 自身的 page cache 之上 ⇒ 48 GB 机器上逼近换页

`tools/make_expert_pool.py` 把这件事搬到离线做一次，产出一个文件；`--expert-pool` 让引擎 mmap 它，
把 host 池直接指过去 ⇒ **零拷贝、零锁页内存、GGUF 的专家页不再进 RAM**。

### 文件格式（自描述）

```
header  64 B   magic "SQEP" | version | flags | n_blocks | n_expert | n_embd | n_ff
                 | n_blocks(u64) | table_off | data_off | total_bytes
table   88 B/块  gu_type | d_type | gu_row | d_row | up_off | down_off | tail_off
                 | bytes | stride | blob_off | scale_off | scale_n
data            每块每专家一个 blob（bytes 有效，stride 对齐的填充为 0）+ 每块 3×n_expert 个 F32 scale
```

blob 布局**就是引擎自己的**：`expert_blob()` 的 `[gate|up|down|tail]` + `native_expert_layout()` 的 stride，
NVFP4 tail 是 `{s_gate, s_up, s_down, 0}`。引擎在读第一个字节前，把文件里**每块每个字段**与模型自身
算出的布局逐项比对，不符即拒（不会读成垃圾）。

### 本模型的精确几何

| 项 | 值 |
|---|---|
| NVFP4 (type 40) | **36 B / 64 值** = 4 × UE4M3 sub-block scale + 32 B E2M1 codes（bpr = 0.5625） |
| 主块专家 blob | `bytes = 1,769,488`，`stride = 1,769,616`（`al = lcm(36,16) = 144`） |
| MTP 块专家（Q8_0） | `bytes = stride = 3,342,336`（`al = lcm(34,16) = 272`，整除） |
| 合计 | 40 主块 × 256 + 1 MTP 块 × 256 = **17.67 GiB**（与引擎实测的池大小精确一致） |

GGUF 里专家张量的排列：`blk.N.ffn_{gate,up,down}_exps.weight` 的 ne 是 `[in, out, experts]`
（专家在最慢维 ⇒ 专家 e 的字节连续），MTP 块是 2D 展平 `[in, out×experts]`（同样连续）。

### 验证

`tools/verify_expert_pool.py` **独立**从 GGUF 重建 blob 再与文件比对（不复用写入器的代码路径）：
**10,496 个专家 blob（41 块 × 256）逐字节一致，0 差异**。

另一个强交叉验证：脚本算出的总大小 **17.67 GiB 与引擎实测的专家池大小精确一致**。

本机产物：`D:\MyLLM\models\experts-q36.bin`（18,976,632,832 B = 17.67 GiB），
生成耗时 **84.3 s**，sha256 前缀 `2025de41…`。

### 待办

- **端到端验证未做**（10-21 16:00 时 8090 被另一会话的 `llama-server.exe` 占满 16024/16311 MiB）：
  需实测 ① 启动时间 ② 输出与无池路径逐 token 相同 ③ CPU 补算速度（mmap 页 vs 锁页内存的差异）。
  **③ 是主要未知风险** —— mmap 页有 page fault 开销，锁页内存没有；变慢则需加预热（启动时顺序 touch 全部页）。
- 未上生产：`strata-ornith-nvfp4-8090.json` 暂未加 `--expert-pool`。
- 对比脚本已备好：`_tmp_code/pool_parity.py`（照 `packed_parity.py` 的模式，A=无池 B=有池，比 token id + READY 时间）。
