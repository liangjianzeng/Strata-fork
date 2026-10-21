# Strata Ornith-NVFP4 项目记忆（热层）

> 三层结构：本文件 = 铁律 + 当前状态（**须 ≤12,900 字节**）；细节 = `MEMORY-details.md`；日粒度 = `YYYY-MM-DD.md`。
>
> **📌 工作目录已迁到 `D:\MyLLM\strata-src`**（含 .git；`D:\MyLLM\` 本身不是仓库）。
> **接手先读 `docs/HANDOVER-NVFP4.md`** —— 当前档位、已完成的引擎改动、
> tokenizer 被删与重建的三个坑、剩余问题都在里面。
> ⚠️ 8090 的 bat/json 仍在 `D:\MyLLM\`（仓库外），本目录 `docs\local` 有 19:28 快照。
> 🔴 **`models\ornith-nvfp4` 的 21.84 GiB 原始权重已被磁盘清理删除**（tokenizer 已重建，权重没恢复）。

## 1. 本机环境铁律（细节见 details §0）- **GPU** RTX 5060 Ti **16GB（16311 MiB）**；CPU 24C/24T；RAM **48GB**；D 盘空闲 ~44 GiB（88% 已用，details §29）。`--threads` 封顶 32。**显存临界 98.2% / 余量 ≈300 MiB** → prefill 一路衰减不收敛；**≤96% 才健康**。
- **🚨 Bash `rm` = 进回收站，空间不释放** → 真删用 .NET `[System.IO.File]::Delete()`；空闲判据 `Get-PSDrive D` 的 `.Free`。⚠️ **清理禁用通配**（曾误删文件）。
- **⚠️ Git Bash 向 cmd 传播 PATH 污染**：bat 里 `timeout /t 1` 命中 coreutils `timeout`，别依赖它延时。Bash 命令串含 `PowerShell`/`cmd` 字样 → 被安全策略拒；`Add-Type` 同拦。查进程用 `tasklist | awk '$2==PID'`。
- **🚨 独显有共享显存**：`nvidia-smi` 只报物理 VRAM 且饱和后失真 ⇒ **禁止用 nvidia-smi 读数推余量**；**判余量只看 Task Manager 两栏**（实测专用 15.0/16.0 GB + 共享 17.9 GB）。

## 2. 服务启停 —— 本环境唯一可靠方式（细节见 details §0.1）- **四条运维铁律**：① **起前先 `netstat -ano | grep :8090` 探活**（重复启动端口占用直接退出，`host exited rc = 1`）；② **宿主 shell 被回收 → 服务跟着没**；判据：任务 failed 且 stdout **无 `host exited rc=` 行** ⇒ 被外部终止（**`rc = 0` = 换宿主，不是崩溃**）；③ **多会话会抢 8090**，停/起前查 PPID + `CODEBUDDY_SESSION_ID`；④ **「重启但保留保活器」**：只杀宿主 `cmd.exe`（Git Bash 下 `taskkill /F` 的 `/F` 被路径化 → 走 Python），保活器 `p.wait()` 返回 → 15s 后自动拉起（**换 bat 必须杀保活器整树**）。

**❌ 全部失败（勿再试）**：`cmd //c <bat>`（被拦但 `$?` 仍 0 极易误判）、`Popen`+DETACHED/BREAKAWAY、`nohup &`、`Start-Process`、`cmd /c start /min`。

- **假就绪**：llama-server 先绑端口再加载权重 → 必须等日志 `model loaded`。**⚠️ .bat 硬规范 GBK + CRLF**（校验 `CRLF==行数 && 孤立LF==0 && GBK可解码`）；症状与修法见 details §0.1，技能 `win-bat-encoding-fix`。
- **端口图**：**8090 = Strata Ornith-NVFP4**（q36）；3080 DSH web 常驻；8097 小模型探针。

## 3.8090 llama.cpp 系休眠备选档（HANDOVER §7；细节 details §1–3,§7,§16–17,§20–21）

- **休眠**：8090 已归 Strata。脚本清单在 details；运行时 `llama.cpp\` = b11539（**CUDA 必须 13.4**，5060 Ti 是 sm_120）。
- **🔴 `n-cpu-moe` 下限 16**（14/15 实测 100K prompt prefill 660→340）。测速/鉴权/push 铁律见 details。

## 4. 高频坑 TOP3

1. **`reasoning_effort` 白名单陷阱**：只认 不传(默认)/`xhigh`/`medium`/`low`/`none`；`high`/`minimal`/其它 → HTTP 500。映射：自动→不传｜高→xhigh｜中→medium｜低→low｜关→none。
2. **KV 类型判据 = GPU 利用率 + 实测 prefill t/s**，不是能不能返回。本 runtime 只 q8_0/q4_0 验证过；q5_1 已判死。**用户铁律：草稿头 KV 必须 f16，禁止 `-ctkd/-ctvd q4_0`**。
3. **`timings.prompt_n` 恒返回 4（bug）** → 算速度一律用 `usage.prompt_tokens`。

## 5. 退化应急（唯一恢复 = 重启，调参无效；details §6）- **变体①「`/` 死循环」**：输出连续 `/` 顶满 max_tokens，content 空、finish=length。**判据用 `/` 占比，不能用 finish=length。**
- **变体②「随机 token 沙拉」**(09-29)：合法 UTF-8 随机字符，chat 500 `peg-native`+MTP 归零。触发：长 hybrid/SWA full re-process。
- **变体③「静默速度/质量退化」**(09-30)：**日志零错误标记**，只有 decode 掉半；旁证 `W srv stop: cancel task`+`f_keep` 0.98→0.148。**~5.9h 即触发**（Bonsai ~23.5h）⇒ 只看错误日志会漏诊。
- **运维铁律**：① 超长上下文任务跑完就重启；② 见 `/` 死循环直接重启别调参；③ 业务侧尽量别中途取消（客户端 max-time 太短把槽 cancel = 变体③旁证）；④ 客户端设 max_tokens+重试熔断。守卫 `bonsai_guard.py`。

## 6. DSH（DeepSeek Harness，details §9）- 配置 = `~/.dsh/profiles/web/cordis.patch.yml`（`settings.yaml` 已废），密钥 `~/.dsh/.credentials.yaml`；端口 **3080**（只绑 127.0.0.1）；**常驻宿主首选 explorer 双击 `C:\Users\jianz\start_dshweb.bat`**（后台保活只活 ~3h）；token 每次重启重生成。改配置不用重启，增删插件必须重启。
- **⚠️ 铁律：模型条目与路由都不写思考档位** —— 默认不传 `reasoning_effort`；别再为"选择器能选档位"加 `reasoningEfforts` 白名单（老会话必挂）；**会话级残留优先级高于配置文件** → 老会话须在 UI 重选模型或新建。搜索 provider 默认 `localxng`。**版本暂不升级**。

## 7. 其他常记（details §16）- **🚨 `--timeout` 永不写 0**：>8KB 请求体全部 400 + body 空 + 日志零记录 ⇒ 8090 新脚本用 3600/600。**这是「所有请求 400 / 上下文坍缩」的第一嫌疑**。**多槽 ctx**：`kv_unified=false` ⇒ `-c` 是「总」KV、被槽数平分。

## 8. Ornith NVFP4 / DFlash / MTP（调研全量见 details §22）

- **模型**：`ornith-ai/Ornith-1.5-35B-A3B-NVFP4`，`qwen3_5_moe`（41 block = 40 主层 + 1 MTP、10 全注意力 + 30 SSM、256 选 8、head_dim 256、ctx 256K），W4A8 23.4GB。**DFlash 是草稿模型不是量化**。**vLLM/SGLang 出局**（SM120 无 FlashInfer FP4 kernel，也无 MoE 专家级 CPU 卸载）。
- **口径**：两条路线都不是「专家全装显存」—— Strata 17.67 GiB 专家在 host 内存、每层仅 21–63/256 进 VRAM；上游 README 的「80 GB RAM」指 Flash-Next 家族，**不适用本模型**。

## 9. Strata q36 两大引擎 bug（✅ 均已修复 2026-10-21；日志 `2026-10-21.md`）- **Bug #1（NVFP4 MMQ 漏 tail scale）**：MMQ 乘积缺 per-expert `weight_scale_2` → 修 = 补 `d_tails` + `MMQ::scale_gu_rows`/`scale_down_rows`。commit `9f318762`。
- **Bug #2（长 prompt 乱码）**：`kv_half_bytes(kvm=0)` 缺 F32 分支返回 int8 的 264 B/cell → **F32 KV 少分配 4 倍**，`pos≥4096` 起 K 写越界砸进 GDN 状态。修 = F32 返回 `hd*4`（一行）。commit `c8d8181f`。**教训：失败面与 walk/tile 路径的相关性是 OOB 命中哪个缓冲的布局假象。**
- **回归**：20/20 匹配文档真值；五模式在 30K prompt 上逐 token 一致 ⇒ **int8/q4_0/k8v4/q8_0 尺寸本来就是对的，Bug #2 只影响 F32（默认 `--kv fp16`）**。配套 commit `428a23c7`（`--kv q8_0`）、`75e052dd`（修 `copy_session` 对 int8 布局拷错）。
- **每 token KV 成本**（11 层 = 10 全注意力 + MTP，HKV=2，HD=256）：F32 44 KiB · q8_0 11.7 · int8 11.3 · k8v4 8.0 · q4_0 6.2。**F32 @256K 需 ~11.0 GiB → 16GB卡装不下**。
- **长 prompt 驱动**：`strata.exe --serve` 的 stdin 协议（`READY` → `GEN <n> <ids>` → `T <tok>` → `DONE`）**不受命令行 32K 长度限制** → `_tmp_code/kvserve.py` / `kvlong2.py`。

## 10. 🔥 当前生产档：Strata Ornith-NVFP4 @8090（2026-10-21 定型，details §23–30）

- **配置** `strata-ornith-nvfp4-8090.json` = `--max-context 262144 --kv q8_0 --expert-slots 48 --prefill auto --mtp 2 --vision` = **单槽 256K + q8_0 KV（2.92 GiB）+ 48 专家槽（3.31 GiB）+ 视觉走 CPU**。启动 `start_strata_8090.bat`（GBK+CRLF，**已注入 `PATH=D:\MyLLM\llama.cpp`**），保活器 `_tmp_code/_start_8090_strata_keep.py`。**decode 实测 36–43 t/s**（比 q4_0 档的 46.5–49.2 低 20–25%，即抬高 KV 精度的代价）。仓库 = `D:/MyLLM/strata-src`（**项目根无 .git**），文档 commit `46a13284` 已 push。
- **🔑 视觉接进来的坑（details §27）**：缺 `cublas64_13.dll` ⇒ 报错 `the vision encoder did not start:` **冒号后为空**（config 无 `"log"` 键 ⇒ stderr 进 DEVNULL）。**mmproj 走 CPU ≠ exe 不依赖 CUDA**；`vision_env()` 只认 `cuda_device` ⇒ 只能 bat 注入 PATH；唯一干净 DLL 副本在 `D:\MyLLM\llama.cpp\`（**该目录不能删**）。CPU 模式两行 `failed to initialize CUDA` / `on the CPU` 是**正常**的。
- **⚠️ `--batch N`（并发请求槽）≠ `--expert-slots N`**：前者让引擎同时解码 N 路，KV 按 `per × n_sess` 算、**每路一整份不共享不分片**，超配 `exit(1)` 不缩容（`q36_engine.cu:1966/:1973`）；与 llama.cpp `--parallel` 语义完全不同。当前档**没有** `--batch`。
- **✅ 长上下文门禁 4/4 全过**（双槽 200K 时代）：28.5K / 57.6K / 116.4K / **192.0K tok** 全部埋点 3/3 命中、`finish=stop`、零乱码。门禁档位须按 ctx 调：超限会被引擎**明确 400 拒绝**（`prompt leaves no room to answer in the context`）—— 引擎不截断，不是 bug。
- **1 GiB 留量之谜已定案**：**不是留给桌面**（空载独显 0 MiB），而是引擎三处留量（`reserve 768 MiB`仅自动算槽路径生效 + `:1842` 双槽安全垫 + `:3300` 工作缓冲，details §24）。**真正可挖的是 packed Q8_0 dense decode 副本 1.28 GiB** ⇒ commit `e705e309` 归还它 ⇒ **专家槽上限 40→48**，bitwise 门禁 token id 完全相同。
- **⚠️ 两种「pack」不是一回事**（details §24）：**A 专家 pack** = 121 个 type40 NVFP4 张量（专家卸载的前提；官方只有 Qwen3.8-Flash-Next 有，但我们的 `-q36.gguf` 自转 ⇒ 形态天然具备）；**B Q8_0 dense decode 副本** = 157 个 Q8_0 张量的第二份拷贝，**与路由专家无关**。⚠️ 上游未定制的 `Ornith-1.5-35B-A3B-NVFP4.gguf` type40 是 **241** 个 —— **不是同一份文件**。
- **离线专家池 `--expert-pool`（details §26）**：离线产 `experts-q36.bin`（17.67 GiB，84 s）⇒ mmap 直读，**省 45.7 s 启动 + 17.67 GiB 锁页内存**。**字节级校验 10,496 个专家 blob 逐字节一致**；**端到端未验证**（主要风险 = mmap page fault 影响 CPU 补算速度），脚本 `_tmp_code/pool_parity.py`，**未上生产配置**。
- **四方交换链**：ctx ↑ → KV 占 ↑ → 专家驻留 ↓ → PCIe 搬运 ↑ → decode ↓。旋钮优先级 `--kv` 降档 > `--expert-slots` > 降 ctx。❌ `STRATA_Q36_NO_MTP_CPU=1` 反向变差。⏳ **DFlash 草稿尚未在 Strata 上试**（用户排序：扩容 → DFlash → 降 KV，前两项已落地）。
- ⚠️ **取结果合并 `content` + `reasoning_content`**（thinking 风格；`max_tokens` 小会 `content=null`+`finish=length` ≠ 故障）。**q8_0 档必须带 `reasoning_budget_tokens`**。⏳ 待办：**DFlash 草稿尚未在 Strata 上试**。
- **⚠️ tokenizer/权重溯源（details §29 + HANDOVER §4）**：**`models\ornith-nvfp4\`（21.84 GiB safetensors）绝不能删** —— 既是转换链源头，又是配置的 `"tokenizer"` 目录。D 盘只剩 ~44 GiB，`models/` 250 GiB，休眠备选 155 G + 真孤儿 29 G 可回收。⚠️ **删文件可能不释放空间**（10-20 删 23.8 GB 空闲未回升）⇒ 清理前先拿小文件验证。

---
**细节见 `MEMORY-details.md`**（按需 grep 章节号）：§0 显卡拓扑 · §0.1 启停实测 · §0.2 Funnel 排障 · §1–3,§7,§16–17,§20–21 llama.cpp 系 · §4–6 KV 精度与退化根因 · §9 DSH · §22 NVFP4/DFlash/MTP · §23 专家扩容 · §24 两种 pack · §25 官方 pack vs q36 · §26 离线专家池 · §27 视觉接入 · §28 batch vs expert-slots · §29 权重溯源与磁盘 · §30 Edit 写 LF 需归一 CRLF
