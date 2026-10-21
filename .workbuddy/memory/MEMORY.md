# Strata Ornith-NVFP4 · 运维记忆（热层）

> **📌 本空间自2026-10-21 起只做运维，不做开发。** 引擎改码、q36 转换、README 文档一律不在此进行。
> 需要开发时另开会话 + 另用工作目录，历史材料在 `D:\\MyLLM\\strata-src`（含 .git）。
> 三层结构：本文件 = 运维铁律 + 当前状态（**须 ≤12,900 字节**）；细节 = `MEMORY-details.md`；日粒度 = `YYYY-MM-DD.md`。
>
> **接手先读 `docs/HANDOVER-NVFP4.md`**。8090 的 bat/json 在 `D:\\MyLLM\\`（仓库外），`strata-src\\docs\\local` 有快照。
> 🔴 `models\\ornith-nvfp4` 的 21.84 GiB 原始权重已被清理删除（tokenizer 已重建，权重没恢复）⇒ **q36 无法重转**。

## 1. 本机环境铁律（细节 details §0）
- **GPU** RTX 5060 Ti **16GB（16311 MiB）**；CPU 24C/24T；RAM **48GB**；D 盘空闲 ~44 GiB（88% 已用，details §29）。`--threads` 封顶 32。**显存临界 98.2% / 余量 ≈300 MiB** → prefill 一路衰减不收敛；**≤96% 才健康**。
- **🚨 Bash `rm` = 进回收站，空间不释放** → 真删用 .NET `[System.IO.File]::Delete()`；空闲判据 `Get-PSDrive D` 的 `.Free`。⚠️ **清理禁用通配**（曾误删文件）。
- **⚠️ Git Bash 向 cmd 传播 PATH 污染**：bat 里 `timeout /t 1` 命中 coreutils `timeout`，别依赖它延时。Bash 命令串含 `PowerShell`/`cmd` 字样 → 被安全策略拒；`Add-Type` 同拦。查进程用 `tasklist | awk '$2==PID'`。
- **🚨 独显有共享显存**：`nvidia-smi` 只报物理 VRAM 且饱和后失真 ⇒ **禁止用它的读数推余量**；**判余量只看 Task Manager 两栏**（实测专用 15.0/16.0 GB + 共享 17.9 GB）。

## 2. 服务启停 —— 本环境唯一可靠方式（细节 details §0.1）
- **四条铁律**：① **起前先 `netstat -ano | grep :8090` 探活**（重复启动端口占用直接退出，`host exited rc = 1`）；② **宿主 shell 被回收 → 服务跟着没**；判据：任务 failed 且 stdout **无 `host exited rc=` 行** ⇒ 被外部终止（**`rc = 0` = 换宿主，不是崩溃**）；③ **多会话会抢 8090**，停/起前查 PPID + `CODEBUDDY_SESSION_ID`；④ **「重启但保留保活器」**：只杀宿主 `cmd.exe`（Git Bash 下 `taskkill /F` 的 `/F` 被路径化 → 走 Python），保活器 `p.wait()` 返回 → 15s 后自动拉起（**换 bat 必须杀保活器整树**）。
- **❌ 全部失败（勿再试）**：`cmd //c <bat>`（被拦但 `$?` 仍 0 极易误判）、`Popen`+DETACHED/BREAKAWAY、`nohup &`、`Start-Process`、`cmd /c start /min`。
- **假就绪**：先绑端口再加载权重 → 必须等日志 `model loaded` / `ready: http://...`。
- **端口图**：**8090 = Strata Ornith-NVFP4**（q36）；3080 DSH web 常驻；8097 小模型探针。8091/8092 已废止。llama.cpp 系备选档全部休眠（details §1–3,§7,§16–17,§20–21）。

## 3. 🚨 .bat 铁律（运维高频坑）
- **改完必须真跑 `cmd /c`** —— GBK+CRLF 校验**不等于**语法校验。2026-10-21 就因此闪退过：块内（`if exist (...)`）出现**未加引号的半角括号** ⇒ cmd 提前闭块 ⇒ `此时不应有 ...。` rc=255、**2 秒退出、服务从未启动**，极易误判成环境问题。块外同样文本能过。
- 硬规范：**GBK + CRLF**（校验 `CRLF==行数 && 孤立LF==0 && GBK可解码`）；技能 `win-bat-encoding-fix`。**别在 bat 仍被 cmd 执行时改写它**。
- 定位用前缀二分 `_tmp_code/bisect_bat2.py`；体检用 `_tmp_code/check_bats.py`（跑全部 bat 报 PARSE OK/FAIL）。
- ⚠️ 判据要看**错误串**，**不能看 rc** —— `netstat|findstr` 查不到端口时正常返回 1。⚠️ **同目录别留两份 start bat**（曾有一个旧副本缺 PATH 注入 ⇒ 视觉报空错误）。
- ⚠️ **Edit 改 md 会写成 LF**，本仓库是 CRLF ⇒ 提交前归一。

## 4. 高频坑 TOP3
1. **`reasoning_effort` 白名单陷阱**：只认 不传(默认)/`xhigh`/`medium`/`low`/`none`；`high`/`minimal`/其它 → HTTP 500。映射：自动→不传｜高→xhigh｜中→medium｜低→low｜关→none。
2. **KV 类型判据 = GPU 利用率 + 实测 prefill t/s**，不是能不能返回。本 runtime 只 q8_0/q4_0 验证过；q5_1 已判死。**用户铁律：草稿头 KV 必须 f16，禁止 `-ctkd/-ctvd q4_0`**。
3. **`timings.prompt_n` 恒返回 4（bug）** → 算速度一律用 `usage.prompt_tokens`。

## 5. 退化应急（唯一恢复 = 重启，调参无效；details §6）
- **变体①「`/` 死循环」**：输出连续 `/` 顶满 max_tokens，content 空、finish=length。**判据用 `/` 占比，不能用 finish=length。**
- **变体②「随机 token 沙拉」**(09-29)：合法 UTF-8 随机字符，chat 500 `peg-native`+MTP 归零。触发：长 hybrid/SWA full re-process。
- **变体③「静默速度/质量退化」**(09-30)：**日志零错误标记**，只有 decode 掉半；旁证 `W srv stop: cancel task`+`f_keep` 0.98→0.148。**~5.9h 即触发**（Bonsai ~23.5h）⇒ 只看错误日志会漏诊。
- **铁律**：① 超长上下文任务跑完就重启；② 见 `/` 死循环直接重启别调参；③ 业务侧尽量别中途取消（客户端 max-time 太短把槽 cancel = 变体③旁证）；④ 客户端设 max_tokens+重试熔断。守卫 `bonsai_guard.py`。
- ⚠️ **`--timeout` 永不写 0**：>8KB 请求体全部 400 + body 空 + 日志零记录 ⇒ 新脚本用 3600/600。**这是「所有请求 400 / 上下文坍缩」的第一嫌疑**。多槽 ctx：`kv_unified=false` ⇒ `-c` 是「总」KV、被槽数平分。

## 6. DSH（DeepSeek Harness，details §9）
- 配置 = `~/.dsh/profiles/web/cordis.patch.yml`（`settings.yaml` 已废），密钥 `~/.dsh/.credentials.yaml`；端口 **3080**（只绑 127.0.0.1）；**常驻宿主首选 explorer 双击 `C:\Users\jianz\start_dshweb.bat`**（后台保活只活 ~3h）；token 每次重启重生成。改配置不用重启，增删插件必须重启。
- **⚠️ 铁律：模型条目与路由都不写思考档位** —— 默认不传 `reasoning_effort`；别再为"选择器能选档位"加 `reasoningEfforts` 白名单（老会话必挂）；**会话级残留优先级高于配置文件** → 老会话须在 UI 重选模型或新建。搜索 provider 默认 `localxng`。**版本暂不升级**。

## 7. 🔥 当前生产档：Strata Ornith-NVFP4 @8090（2026-10-21 定型）
- **配置** `strata-ornith-nvfp4-8090.json` = `--max-context 262144 --kv q8_0 --expert-slots 48 --prefill auto --mtp 2 --vision` = **单槽 256K + q8_0 KV（2.92 GiB）+ 48 专家槽（3.31 GiB）+ 视觉走 CPU**。启动 `start_strata_8090.bat`（已注入 `PATH=D:\\MyLLM\\llama.cpp`，视觉编码器缺 `cublas64_13.dll` 就起不来）。保活器 `_tmp_code/_start_8090_strata_keep.py`。
- **实测 decode 36–43 t/s**（比 q4_0 档的 46.5–49.2 低 20–25%，即抬高 KV 精度的代价）。**每token KV 成本**（11 层，HKV=2，HD=256）：F32 44 KiB · q8_0 11.7 · int8 11.3 · k8v4 8.0 · q4_0 6.2 ⇒ **F32 @256K 需 ~11 GiB，16 GB 卡装不下**。
- **离线专家池 `--expert-pool` 已上生产**：`experts-q36.bin`（17.67 GiB，mmap 直读）。干净环境 **READY 56.1→29.8 s**、24 token 与无池逐位相同。⚠️ **页缓存必须已热**（冷启动 144 s，比无池还慢一倍）⇒ bat 里先读一遍预热。⚠️ 启动时间只能看同轮内 `place_experts`，READY 会被外部 CPU 竞争污染（曾污染出 462 s）。
- **四方交换链**：ctx ↑ → KV 占 ↑ → 专家驻留 ↓ → PCIe 搬运 ↑ → decode ↓。旋钮优先级 `--kv` 降档 > `--expert-slots` > 降 ctx。
- **⏳ 运维待办**：① **视觉仍未发真图验证**（编码器本身已单独验证：5.8 s 即 `READY 2048`）；② `--expert-pool` 只做过单次 24 token 对拍，未跑长时间；③ DFlash 草稿未在 Strata 上试；④ 原始 safetensors 已删 ⇒ **q36 无法重转**，换档前先确认这一点。

## 8. 已归档的开发内容（只读，勿在本空间改动）
以下都**不是运维必需**，细节全在 `MEMORY-details.md` 与 `docs/HANDOVER-NVFP4.md`：
- **q36 两大引擎 bug 已修并入库**（`c8d8181f` F32 KV 少分配 4 倍 → 长 prompt 乱码；`9f318762` NVFP4 MMQ 漏 tail scale）⇒ 运维只需知道 **KV 模式矩阵四档已全量对拍过、int8/q4_0/k8v4/q8_0 无问题**，勿再排查乱码。details §24,§31。
- **两种「pack」的区分**（专家 pack vs Q8_0 dense decode 副本）、**官方 pack 机制不适用本模型**、**专家池实现细节**、**NVFP4/DFlash/MTP 调研**、**权重溯源**、**q36 转换链** —— details §22–§26,§29。
- **🔴 `n-cpu-moe` 下限 16**（llama.cpp 系，14/15 实测 100K prompt prefill 660→340）—— 仅在切回 llama.cpp 备选档时需要。CUDA 必须 13.4（5060 Ti 是 sm_120，12.4 不支持）。
---
**细节见 `MEMORY-details.md`**（按需 grep 章节号）：§0 环境/显卡拓扑 · §0.1 启停实测 · §0.2 Funnel 排障 · §1–3,§7,§16–17,§20–21 llama.cpp 系 · §4–6 KV 精度与退化根因 · §9 DSH · §22–§26 开发调研与专家池（**只读**）· §27–§28 视觉与生产档 · §29 权重溯源与磁盘 · §30 Edit 写 LF · §31 tokenizer 重建三坑
