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

- 数值对拍（Strata vs llama.cpp 输出）未做；lm_head 走了 NVFP4→Q8_0 重量化，与原始 NVFP4 有轻微量化损失（与 shexp 同级）。
- 磁盘：中间产物 `Ornith-1.5-35B-A3B-NVFP4-fix.gguf`（23.8GB）已删除；HF 原始权重完整保留于 `D:\MyLLM\models\ornith-nvfp4\`（22GB），可随时重建。注意：D 盘删除 fix.gguf 后空闲空间未回升（21.7GiB，疑沙箱软删除暂存），重建文件前需先核实。
- 相关文件：`D:\MyLLM\_tmp_code\make_q36_gguf.py`、`fix_router_shape.py`；patch 源码 `strata-src/src/qwen35/q36_engine.cu`、`strata-src/include/strata/core/family_layout.hpp`；编译 `build_strata2.ps1`/`resume_strata2.ps1` → `strata-build2\strata-q36.exe`。
