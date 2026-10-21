# Strata 原生运行 Ornith-1.5-35B-A3B-NVFP4 —— 基线报告（2026-10-20）

## 结论

**Strata 0.1.45.3（q36 引擎）原生跑通 NVFP4 权重，prefill 较 llama.cpp 路线约 2 倍，decode 约 1.5 倍。**

| 指标 | Strata 原生 (q36) | llama.cpp b11539 (NVFP4) | llama.cpp +DFlash |
|---|---|---|---|
| prefill pp2048 | **1100–1282 t/s** | ~600 t/s | — |
| decode tg128 | **47–55 t/s** | 30–39 t/s | 45.7–67.0 t/s |

- Strata 无需 draft 模型；tg 与 llama.cpp+DFlash 相当，且三档重复离散 ±8%（专家池 CPU 摊薄波动）。
- 显存占用：dense+KV+缓冲 ≈ 4.34 GiB GPU + 专家池 4.35 GiB = **~8.7/16 GB**，余量 6.7 GiB。

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

- 乱码检测（替换字符 / 斜杠失控 / 4-gram 重复 / 单字沙拉）**全档零触发**。
- 208K 长请求之后再发短请求：FFT 解释正常、"北京" 1.1 s、1234×5678=**7006652** 精确 → **长请求不污染后续会话的 KV**。
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

