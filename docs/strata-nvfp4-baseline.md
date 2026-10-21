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
| **decode tg128 @256K ctx（生产档）** | **29–33 t/s** | ctx 封顶 96K | ctx 封顶 96K |
| decode 裸 forward（÷ mean len） | 47–55 t/s | 35–40 t/s | **~20 t/s**（3.53–3.75 tok/forward） |
| 上下文上限（16 GB 卡） | **256K**（q8_0 KV，2.92 GiB） | 96K（脚本实测上限） | 96K（脚本实测上限） |
| 208K prompt 正确性 | **3/3 埋点命中，零乱码** | 未测 | 未测 |

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
| **Strata q36** | **全部 17.67 GiB 在锁页 host 内存**（`cudaHostAlloc`），每层只让 7–63 / 256 个进 VRAM（0.48–4.35 GiB） | 未驻留的专家走 16 线程 CPU pool 算，prefill 的大 chunk 经 PCIe 流式送入（1.59 GiB staging） |
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
      · 120 个 routed experts (blk.0-39 × gate/up/down) 保留 NVFP4 + .scale F32
      · 120 个 shexp NVFP4 → Q8_0（dequant+requant，含 weight_scale_2）
      · 201 个 dense BF16 → Q8_0（含 MTP 层 BF16 专家、attention 投影）
      · 553 个原样（router F32、norms、token_embd BF16、output.weight NVFP4+.scale）
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

> 下表是 **q8_0 KV 档**的结果。**q4_0 + 48 slots 档的复测见下一节**（当前生产档，已 ALL CLEAN）。

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

### q4_0 KV + 48 slots 生产档复测（2026-10-21 晚，当前生产配置）

`--max-context 262144 --kv q4_0 --expert-slots 48 --mtp 2`，峰值显存 15218 MiB。
q4_0 是**精度最低的 KV 档**（每 32 值一个 fp16 scale），上生产前必须重跑同一套验收。

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
| 256K / q8_0 + 21 slots（旧生产档） | 2.89 GiB | 7（0.48 GiB） | **8.1%** | 29–33 t/s（长 prompt 10.3） |
| **256K / q4_0 + 48 slots（当前生产档）** | **3.31 GiB** | **48（3.31 GiB）** | **24.5%** | **46.5–49.2 t/s** |

- 这是**上下文长度、KV 精度、专家驻留、decode 速度的四方交换**：要 256K 就得压 KV 档位，
  压出来的显存换成专家驻留，命中率上去后 decode 才回来。
- ⚠️ llama.cpp+DFlash 的 45.7–67 t/s 是在 **96K ctx** 下测的，与本表 256K 档**不构成同档对比**。

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

专家池已经吃掉 3.31 GiB，256K 的 KV（q4_0，1.55 GiB）也已到位。剩下的旋钮：

| 手段 | 效果 | 代价 |
|---|---|---|
| 改 `need > fr` 核算让其动用 768 MiB reserve | 理论上可再放 ~10 slots（≈56） | 改引擎显存核算，需重编译 + 重跑 20/20 回归 |
| `--kv k8v4`（5.0 KiB/token，比 q4_0 再省 0.37 GiB） | 多 ~7 slots | KV 精度档更激进，尚未做长上下文对拍 |
| `--max-context` 降到 192K | 专家驻留回 55/256，decode 43+ | 放弃 256K 能力 |

> **当前生产档** `strata-ornith-nvfp4-8090.json` = `--max-context 262144 --kv q4_0 --expert-slots 48 --mtp 2`
> （`start_strata_8090.bat` 同步）。实测峰值显存 15218 MiB，短问答 3 题全通过
> （MoE 解释正常、翻译正常、`1234*5678=7006652` 精确）。
> q4_0 是精度最低的 KV 档，上生产前必须过长上下文验收 —— 见下节。

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

