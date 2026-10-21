# Strata Ornith-NVFP4 交接说明（2026-10-21 19:30）

> **新的工作目录是 `D:\MyLLM\strata-src`**（不是 `D:\MyLLM`）。本文是给本目录后续会话的入口，
> 汇总已经完成的工作、当前档位、以及**尚未解决的问题**。

---

## 0. 三条最要紧的事

1. **磁盘清理误删了 tokenizer** —— `models\ornith-nvfp4\`（21.84 GiB原始权重 + tokenizer）已被删除，
   tokenizer 已从 ModelScope 重建（见 §4），**原始权重没了，重转链条断了**。
2. **仓库根目录才有 `.git`** —— `D:\MyLLM` 不是 git 仓库；所有 commit/push 都在本目录。
3. **8090 的 bat/json 在 `D:\MyLLM\`（仓库外）** —— 本目录 `docs\local\` 有一份 19:28 的快照留档。

---

## 1. 当前生产档

`D:\MyLLM\strata-ornith-nvfp4-8090.json`（启动 `D:\MyLLM\start_strata_8090.bat`）：

```
--max-context 262144  --kv q8_0  --expert-slots 48
--prefill auto  --mtp 2  --vision  --expert-pool D:/MyLLM/models/experts-q36.bin
```

| 项 | 值 |
|---|---|
| 档位 | **单槽 256K**（**无`--batch`**）+ q8_0 KV（2.92 GiB）+ 48 专家槽（3.31 GiB）+ 视觉走 CPU |
| decode | **36–43 t/s**（比 q4_0 档的 46.5–49.2低 20–25%，即抬高 KV 精度的代价） |
| 视觉 | `gpu:false` ⇒ BF16 mmproj 899 MB落 host RAM，不占显存 ⇒ 48 槽保住 |
| 引擎 | `strata-build2\strata.exe`（q36 fork） |
| 服务 | `python -m serve.server --engine strata --config ... --port 8090` |

**bat 硬规范**：GBK + CRLF。校验 `CRLF行数 == 总行数 && 孤立LF == 0 && GBK可解码`。
症状与修法见 `MEMORY-details.md` §0.1，技能 `win-bat-encoding-fix`。

---

## 2. 已完成的引擎改动（全部已提交推送）

| commit | 内容 |
|---|---|
| `9f318762` | 修 Bug#1：NVFP4 MMQ 漏 per-expert `weight_scale_2`（尾包 scale） |
| `c8d8181f` | 修 Bug#2：`kv_half_bytes` F32 分支返回 `hd*4`（原来返回 int8 的 264 B/cell ⇒ F32 KV 少分配 4 倍 ⇒ 长 prompt 乱码） |
| `428a23c7` | 新增 `--kv q8_0` |
| `75e052dd` | 修 `copy_session` 对 int8 布局拷错字节 |
| `e705e309` | 显式 `--expert-slots` 装不下时归还 packed Q8_0 decode 副本（1.28 GiB）⇒ 专家槽上限 **40 → 48** |
| `f9fa03c2` | **新增 `--expert-pool <file>`：从离线池文件mmap 直读路由专家** |
| `719f530b` `4a1582ed` `e0a66726` `46a13284` | 文档 |

### 离线专家池（`--expert-pool`）—— 已端到端验证

| 指标 | 无池 | 有池 |
|---|---|---|
| READY | 56.1 s | **29.8 s** |
| place_experts | 44.2 s | **3.9 s** |
| CPU 池补算 2745 行 | 343 ms | **117 ms** |
| **token id** | — | **24/24 逐位相同** |

⚠️ **必须页缓存已热才划算**：冷盘实测 READY 144 s（比无池的 54.5 s 还慢一倍）。
`start_strata_8090.bat` 已加预热步骤（读一遍 17.67 GiB，约 7 s）。
⚠️ 池文件本身 17.67 GiB，D 盘只剩 ~113 GB。

---

## 3. 长上下文门禁（已过）

| 档位 | prompt tokens | 埋点命中 | finish |
|---|---|---|---|
| ~25K | 28,539 | 3/3 | stop |
| ~50K | 57,638 | 3/3 | stop |
| ~100K | 116,437 | 3/3 | stop |
| ~180K | 192,037 | 3/3 | stop |

零乱码、零 `/` 死循环、零 token沙拉。
⚠️ 门禁档位须按 ctx 调：超限会被引擎**明确 400 拒绝**（`prompt leaves no room to answer in the context`）—— 引擎不截断，不是 bug。
⚠️ **取结果合并 `content` + `reasoning_content`**（thinking 风格）；`max_tokens` 小会 `content=null` + `finish=length` ≠ 故障。**必须带 `reasoning_budget_tokens`**。

---

## 4. 🔴 tokenizer 被删与重建（本目录会话必须知道）

**事件**：磁盘清理把 `D:\MyLLM\models\ornith-nvfp4\` 整个删了（D 盘从 44 GiB → 113 GB，
释放的 69 GB ≈ 122B 的 46.96 + ornith-nvfp4 的 21.84）。该目录**双重身份**：
① HF safetensors 原始权重（转换链源头）② 8090 配置的 `"tokenizer"` 目录。
记忆里当时标了「**绝不能删**」，但清理时还是删了。

**后果**：`serve/server.py:7697` 硬检查 `vocab.json`，缺则直接 `ap.error(...)` 退出 ⇒ **服务起不来**。

**已修复**（`tools/local/rebuild_tokenizer.py` + `fix_vocab.py` + `verify_tokenizer.py`）：
从 `https://www.modelscope.cn/models/ornith-ai/Ornith-1.5-35B-A3B-NVFP4/resolve/master/`
只下了 5 个小文件（21.84 MiB，6 秒），**没有重下21.84 GiB 权重**。

**三个必须知道的坑**（重建时踩到的）：

| 坑 | 现象 | 解法 |
|---|---|---|
| **ModelScope 的 `vocab.json` 只有 248044 项，不含 added_tokens** | `ST.Tokenizer`（`tools/strata_tokenizer.py:107`）执行 `self.special_tokens[tokens[i]] = i` ⇒ `IndexError` | **把 26 个 added_tokens 追加进 vocab.json**（共 248070），`token_type.json` 同步写 248070 长 |
| `merges.txt` 末尾换行| `split("\n")` 多出一个空串 ⇒ `merge 247587 is not a pair: ''` | 剥离尾部空行，剩 **247587** 个合法 pair |
| `token_type.json` 是 Strata setup 的**派生产物**，ModelScope 没有 | 得自己生成 | 规则：`1=NORMAL`、`3=CONTROL`，CONTROL 取 `tokenizer.json` 的 `added_tokens` 里 `special: true`（本模型 **14 个**，id 248044–248057） |

**验证结果**：`strata_tokenizer` 构建成功，5/5 往返全过（中文 8 ids / 英文 10 ids / 代码 13 ids）。
词表规模与删除前服务运行时的记录一致。

⚠️ **重建的 `merges.txt` 我改过**（去了尾部空行），与ModelScope 原文件**不完全字节相同**。
若要严格可复现，请连同 `fix_vocab.py` 一起用。

---

## 5. ⏳ 后续剩余问题

### 5.1 破坏性：原始权重已丢，重转链条断了

`models\ornith-nvfp4\` 的 21.84 GiB safetensors 没了。若要重转 q36 GGUF：
- 需要重新下载 3 个分片（10.0 + 10.0 + 3.4 GB）
- `tools/local/make_q36_gguf.py` 的输入是 `Ornith-1.5-35B-A3B-NVFP4-fix.gguf`，**该中间文件也已不存在**
- 完整链：`safetensors → convert_hf_to_gguf.py (NVFP4 repack) → fix_router_shape.py → make_q36_gguf.py`

### 5.2 功能待验

- **视觉未端到端验证过**：能起（`READY 2048`，6.2 s）且CPU 模式日志正常，但**没有发过真图验证问答**
  ⇒ 用 `tools/local/vision_probe.py` 发真PNG。
- **`--expert-pool` 未在生产配置跑过长时间**：只验证过单次 GEN（24 token）的对拍与启动时间。
- **DFlash 草稿尚未在 Strata 上试**（用户排序：扩容 → DFlash → 降 KV，前两项已落地）。
  命令形如 `--model-draft <q8_0 gguf> --spec-type draft-dflash --spec-draft-n-max 4`（本地有 q8_0 402MB 与 bf16 736MB）。

### 5.3 性能口径的坑

- **`nvidia-smi` 在WDDM 下只报物理 VRAM 且饱和后失真** ⇒ **禁止用它推余量**，只看 Task Manager 两栏。
- mmap vs 锁页的对比**必须在页缓存已热、且无其他服务抢 CPU 时测**。本机已实测污染轮：
  无池 READY 被拉到 462 s（正常 56 s），差 8 倍。**测速前先确认 8090 无监听、无大内存进程。**

---

## 6. 材料清单（本目录）

| 路径 | 内容 |
|---|---|
| `tools/local/` | 10 个脚本：q36 转换、专家池生成/校验/对拍、tokenizer 重建/验证、配置检查、磁盘报告、视觉探针 |
| `docs/local/` | `start_strata_8090.bat` / `stop_strata_8090.bat` / `strata-ornith-nvfp4-8090.json`（19:28 快照） |
| `docs/strata-nvfp4-baseline.md` | **完整基线报告**：性能对比、KV 五档、显存账、两种 pack 的澄清、门禁数据 |
| `README.md` / `README.zh-CN.md` |顶部 NOTE 是当前档位速查 |
| `tools/make_expert_pool.py` `tools/verify_expert_pool.py` | 已入git 的正式工具 |
| `.workbuddy/memory/` | `MEMORY.md`（热层，12,888 字节）+ `MEMORY-details.md`（细节，§0–30） |

---

## 7. 环境铁律（速查）

- **GPU** RTX 5060 Ti 16GB（16311 MiB）；RAM 48 GB；CPU 24C/24T。**显存 ≤96% 才健康**，98.2% 是临界。
- **5060 Ti 是 sm_120（Blackwell）** ⇒ CUDA 必须 **13.4**，12.4 不支持。
- **`D:\MyLLM\llama.cpp\` 不能删** —— 它提供 `cublas64_13.dll`，视觉编码器启动必需
  （`strata-vision.exe` 即使走 CPU 也要链接 CUDA 库）。
- **起服务前先 `netstat -ano | grep :8090` 探活。**
- **Bash `rm` 进回收站不释放空间** —— 真删用 .NET `[System.IO.File]::Delete()`；清理**禁用通配**。
- **Edit 工具写 md 会把行尾改成 LF**，本仓库 `core.autocrlf=true` ⇒ 提交前归一 CRLF。
- **GitHub push 走 `ssh://git@ssh.github.com:443/...`** —— `github.com` HTTPS 被 SNI 选择性阻断。
- **`.bat` 改写时不能在 cmd 正执行它** —— 会按字节偏移续读新文件、意外重执行整个脚本。

---

## 8. 记忆文件写法（本目录新增教训）

改 `MEMORY.md` 时**先测量再动手**：

1. 先打印各章/各行字节数，按体积排序，从最大的砍
2. **一次成型**，不做「压一点再看」的循环
3. **只删不重写** —— 细节下沉 `MEMORY-details.md`，热层留结论 + 章节指针
4. 匹配**必须用行结构**（按 `\r\n` 拆行），不要手写长字符串 `replace` —— CRLF 下必然失配

2026-10-21 教训：压到限内用了 8 个脚本，前 6 个白跑。