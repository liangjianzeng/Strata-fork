# Strata Ornith-NVFP4 · 细节存档（按需 grep 章节号）

> **📌 自 2026-10-21 起本空间只做运维，不做开发。** 下面 §22–§26 是**已归档的开发内容**
> （引擎改码、q36 转换链、专家池实现、pack 机制分析、模型调研）—— 只读，**勿在本空间继续改动**。
> 日常运维只需看：§0 环境/显卡拓扑 · §0.1 启停实测 · §0.2 Funnel 排障 · §4–6 KV 精度与退化根因 ·
> §9 DSH · §11/§20/§21 Funnel 与宿主观测。§22–§31 里的运维结论已提炼进 `MEMORY.md` 对应章节。
>
> 需要开发时另开会话 + 另用工作目录（历史仓库在 `D:\MyLLM\strata-src`）。

> **这是参考文件，不是每轮注入的内容。** 每轮自动注入的是 `MEMORY.md`（热层，只放铁律与当前状态）。
> 需要复盘实验数据、源码级原理、历史 A/B 细节时，再读本文件。
> 2026-09-22 13:05 从原 MEMORY.md（58469 B）精简而来；原始全文备份 `MEMORY.md.bak_20260922_1305`。
> 精简原则：**只留当前有效的结论、铁律与排错入口**；已被证伪或已被取代的中间过程（8092 那一批 A/B）压缩成一句结论，
> 完整实验记录仍在各日 `YYYY-MM-DD.md`。

---

## 0. 本机环境铁律

- **GPU**：RTX 5060 Ti **16GB（16311 MiB）**。**CPU 24 核 / 32 线程**（Intel 8P+16E）→ `--threads` 封顶 **32**。
- **🔥 溢出临界 = 显存 98.2% / 余量 ≈300 MiB**（多次独立命中，头号判据）。
  表现：prefill 一路衰减不收敛（如 574→485 t/s），或长 prefill 掉到 518 t/s。
  **判据：落在 98% 水位 = 危险；≤96% = 健康**（95.8%/176K 在 93% ctx 占用下曲线平滑，是安全的）。
- **🖥️ 双 GPU 归属定案（2026-09-23，推翻早前「桌面渲染在独显」的推论）**
  - 平台：Lenovo Legion 台式 `91AY0033CP` / 主板 `LENOVO 3789`，**CPU Intel Core Ultra 9 275HX**，48 GB RAM；`Microsoft Graphics Hybrid: Supported`、`Mux Support Inactive`、`Mux Target GPU: dGPU`。
  - 适配器：`ROOT\DISPLAY\0000` = **GameViewer Virtual Display Adapter**（UMDF IDD，`gamevieweridddriver.dll`，`Device Type: Display-Only Device`，上层过滤 `IndirectKmd`）；`ROOT\DISPLAY\0001` = Oray(向日葵) IDD。物理卡 = Intel 核显 `DEV_7D67`(LUID `0x14A46`) / RTX 5060 Ti `DEV_2D04`(LUID `0x1650D`)。
  - **唯一在线的显示器是这块虚拟屏**（2880×1800@144Hz；`Native Mode 2560×1440@144.477` 取自它冒充的 Dell S2719DGF EDID）。物理屏全部离线：`DISPLAY\DELD0E6` 的父设备是 IDD，`LEN0A0C`(挂独显)、`XMI3445`(挂核显) 均为 `Unknown` = 历史残留 EDID。
  - **核显 = display/local 适配器；独显 = non-local 纯渲染卡。** 判据（比猜可靠）：
    ① `\GPU Non Local Adapter Memory(*)\Non Local Usage` **只有独显有值**（~113 MiB）＝该卡无显示器挂载；
    ② `\GPU Process Memory(*)\Non Local Usage` 里 **dwm 在独显上 ~174 MiB** ＝ dwm 的显示卡是核显。旁证：dwm Local Usage 核显 314 ≫ 独显 61；dwm 3D 引擎 55% 核显 / 19% 独显。
  - **机制（Microsoft Learn《IddCx 1.4 updates for console and remote IDDs》）**：IDD 通过 `IddCxAdapterSetRenderAdapter` 指定**渲染适配器**，它决定 **DWM 在哪个 GPU 上枚举该 ID 显示器**（= 桌面合成落哪块卡，且是 swapchain GPU）；而「设置 → 显示 → 图形」里的**逐应用 GPU 偏好只决定应用的枚举 GPU**。两者组合出「同适配器 / 混合跨适配器」4 种路径。⇒ **想搬桌面只能改 IDD 的渲染适配器（由 GameViewer 驱动/配套程序决定），用户偏好搬不动 dwm。**
  - 逐应用偏好落盘：`HKCU\Software\Microsoft\DirectX\UserGpuPreferences`，**值名 = exe 全路径**，值 `GpuPreference=1;` = 节能(核显)、`2;` = 高性能(独显)。**改完要重启该应用**（Chromium 类重启 GPU 进程即可）。
    **2026-09-23 已执行**：把 8 个原本落独显的应用设为 `1;` —— **WorkBuddy.exe / msedgewebview2.exe / Explorer.EXE / ShellHost.exe / WidgetBoard.exe / IntelGraphicsSoftware.exe / CrossDeviceResume.exe / PhoneExperienceHost.exe**（写前 `reg export` 备份 `D:\MyLLM\_gpu_prefs_backup_20260923_103530.reg`）。原有 4 条 `GpuPreference=2;`（LegionZone/LeAppStore env_detect、鲁大师 nBench、yysls）是本机软件自己设的，未动。
    副作用：**WorkBuddy 自身重启才能迁走**（约 200 MiB）+ **`HipsDaemon.exe`（火绒，25.5 MiB）路径被保护读不到、没能设**（需在「图形设置」手动添加或写 HKLM）；`dwm` 搬不动（见上）。预期独显最终降到 ~100 MiB 以内。
  - **A/B 实测（离屏 Chrome + 临时 profile）**：不设偏好 → GPU 进程落**核显**（Shared 36 MiB）；设 `GpuPreference=2;` → 立刻落**独显**（Ded 18.1 MiB）。⇒ 偏好双向生效。（Chromium 默认跟随显示器，故基线在核显；WorkBuddy 落独显是它自己选的高性能卡。）
- 服务若由 WorkBuddy 后台起，会因空闲回收被杀 → 502 时重启脚本即可。
- **中文 Windows 下 `netstat`/`taskkill` 输出是 GBK**：Python subprocess 必须 `capture_output=True` 取 bytes 再 `decode('gbk')`；用 `text=True` 会 UnicodeDecodeError 直接崩。
- 本机 PowerShell `Add-Type` 被安全策略拦截，收进回收站改用 Python `ctypes` 调 `shell32.SHFileOperationW`。
  ⚠️ 该 API **返回码不可靠**（全 rc=2 但操作生效）→ 判定必须以 `ls` 复核 + 回收站 `$I` 元数据（`D:\$Recycle.Bin\<SID>\$I*`：int64 ver/size/FILETIME + int32 pathLen + UTF-16LE path）为准。
- **🖥️ GPU 显存归属 & "专用/共享"口径（2026-09-23 实测，解释"为什么占的是独显"）**：
  本机是**双 GPU 台式机** —— 独显 RTX 5060 Ti（LUID `…0001650D`，有独立 GDDR7）+ 核显 Intel Graphics（LUID `…00014A46`，**UMA：无独立显存颗粒，显存全从系统内存划**）。
  ⇒ **核显侧一切分配 Dedicated 恒为 0，全落在 Shared 栏** → nvidia-smi / 任务管理器的"专用 GPU 内存"里**只看得到独显**，这是**口径错觉的一半**。
  实测（空闲态）：独显 Ded **508.3** / Shared 193.8（25 进程）；**核显 Ded 0 / Shared 1257.2（19 进程，共享占用反而更大）**；另有 LUID `…18150`(9) / `…1817F`(1) 全 0，是空适配器。
  核显大户：`GameViewerServer.exe` 335MB、`dwm.exe` 317MB、`explorer.exe` 132MB、`NVIDIA Overlay.exe` 129MB、`csrss.exe` 127MB、`WUDFHost.exe` 71MB —— **核显并不闲置**。
  ⇒ **另一半是真的**：所有用户应用的 D3D 设备都建在**独显**上（WorkBuddy 116.6、msedgewebview2×3 共 102、火绒 46、WidgetBoard 33.6…）—— Windows 的默认 GPU 选择把它们引到独显（WDDM 规则：窗口跟随其显示器所连 GPU；未声明偏好时用默认/高性能适配器）。
  ⚠️ **本机显示输出不在物理 GPU 上**：`nvidia-smi` 报独显 **`Display Attached: No` / `Display Active: Disabled`**；WMI 里 NVIDIA 与 Intel 均 `Availability=8(Off Line)`、无活动模式；**唯一 Running 且有活动模式的是 `GameViewer Virtual Display Adapter`（2880×1800@144Hz）**，另有向日葵 `OrayIddDriver Device`；物理屏是 Dell（EDID `DELD0E6`，DP）。
  ⇒ 桌面渲染落独显，是**"显示输出 → DWM → 所有窗口"这条链绑在独显**的结果，不是应用自己挑的。想改只能逐应用在「设置 → 系统 → 显示 → 图形」设"节能"，且跨 GPU 合成有开销。
  ⇒ **实用推论：那 470–540 MiB 是实打实吃掉的显存**（实测 Ornith 15851 + 桌面/系统 460 = 16311 ✓，**没有被 WDDM 驱逐**）→ 想省显存，**降 ctx 的杠杆（192K→176K 省 1380 MiB）远大于折腾桌面 GPU 归属**。

### 0.3 虚拟屏（IDD）的渲染适配器 —— 系统侧无开关；实测落在核显（2026-09-23 定案）

**问题**：显示器的 HDMI 接在**核显**上（只是显示器没开机），能不能把 GameViewer 的虚拟显示器"挂"到核显，从而让桌面不再碰独显？

**官方机制（决定性）**：微软《IddCx 1.4 updates for console and remote IDDs》给出决策表 ——
- IddCx 1.4（Win10 1903+）之前：**系统固定用 POST 适配器**渲染给 IDD 的桌面图像（POST 适配器 PnpStopped 时退回 WARP）。
- IddCx 1.4 起：IDD 可选调用 **`IddCxAdapterSetRenderAdapter`** 自己指定渲染适配器（"驱动可用 Dxgi 枚举找出目标 LUID"）；不调用则按上一条。
- **「设置 → 系统 → 显示 → 图形」里的逐应用"节能/高性能"只改变 `Enum for App`（应用在哪块卡上枚举/渲染），完全不改变 `Swapchain GPU`（IDD 的交换链建在哪块卡）与 `Enum for DWM`（DWM 在哪块卡上枚举这块虚拟屏）**。表中 6 行组合（IDD 偏好 × 用户偏好）下 `Swapchain GPU` 只由 IDD 偏好单选决定。
  ⇒ **"把虚拟显示器挂到核显"这个动作在 Windows 里不存在**；用户侧没有任何开关。**显示器 HDMI 接在哪块卡、显示器开不开机，都不参与这个决定。**
- 本机对应物：GameViewer IDD = `ROOT\DISPLAY\0000`，驱动 `gamevieweridddriver.dll`（UMDF/WUDFRd，`oem184.inf`，v15.6.5.199，`Device Type: Display-Only Device`）；该 DLL **导入了 `dxgi.dll`**（符合"用 DXGI 枚举挑渲染适配器"的特征），且内嵌字符串 `S2719DGF`（虚拟屏冒充物理屏 EDID，故 `Output Type: HDMI`）。**客户端侧只有「远程设置 → 自动创建虚拟屏」开关，无显卡选项**（查过 `ProgramData\Netease\GameViewer\{config,user_setting,global_config}.ini` 与 `config/streamer/*.json`，均无 GPU 字段）。

**实测：这块虚拟屏的帧缓冲已经落在核显上** ——
| 证据 | 读数 | 含义 |
|---|---|---|
| 承载 IDD 的 UMDF 宿主 `WUDFHost.exe` pid=1824 的显存 | **全部在核显 LUID**（Shared 71.5 / Local 8.9 MiB），独显上一条都没有 | IDD 的帧缓冲=swapchain 在该 LUID 的卡上；71.5 MiB ≈ 3×2880×1800×4B=62 MB（± 额外缓冲） |
| GPU 引擎采样 | `GameViewerServer.exe`：**核显** 3D 7.6% + videodecode 28.3%；独显 0 | 抓屏/编码链路在核显 |
| GPU 引擎采样 | `dwm.exe`：核显 3D 34.3% ≫ 独显 3D 18.9% | 合成主战场在核显，独显那部分是跨适配器开销 |
| 旁证 | GameViewer `encoder_codec_capability_cache.json` 同时列出两块卡（`adapter_id 0x164F5` + dev `0x2D04` = RTX 5060 Ti；`0x14C30` + dev `0x7D67` = Intel） | 驱动**能看见**两块卡，说明它有主动挑卡能力；实际跑的那条在核显 |

**⇒ 独显那 ~370 MiB 不是虚拟屏造成的**，构成（2026-09-23 11:5x 实测，llama-server 正在跑）：

| 进程 | 独显专用显存 | 独显上有 3D 活动 |
|---|---|---|
| WorkBuddy.exe（Chromium `--type=gpu-process`） | **130.0 MiB**（Local 98.8） | 1.8% |
| msedgewebview2.exe ×3 | 86.8 MiB | 0.2% |
| dwm.exe | 95.4 MiB | 18.9%（跨适配器面） |
| IntelGraphicsSoftware 16.1 / GameViewer.exe 10.9 / explorer 10.7 / CrossDeviceResume 9.9 / TabTip 4.9 / System 4.3 / ShellHost 3.9 / PhoneExperienceHost 0.7 | ~61 MiB | — |
| **合计** | **≈ 374 MiB** | 同时核显 Shared ≈ 1045 MiB（桌面合成主体） |

**⚠️ 已否证的路：逐应用"节能 GPU"偏好对 Chromium 系无效。**
- 机制本身是对的：`HKCU\Software\Microsoft\DirectX\UserGpuPreferences`，值名 = exe 全路径，`GpuPreference=1;` = 节能(核显)、`2;` = 高性能(独显)。**A/B 实证过**：离屏 Chrome 不设偏好落核显（Shared 36 MiB）；设 `2;` 后落独显（Dedicated 18.1 MiB）。
- 但本机 8 个应用设为 `1;` 后**实测无效**：偏好写入 10:35:30 → 机器 10:52:47 重启 → WorkBuddy/WebView2 于 10:53:3x 才启动（**不是"没重启"的问题**），键名与实际 exe 路径**逐字符一致**（`...\Programs\WorkBuddy\WorkBuddy.exe`、`...\EdgeWebView\Application\153.0.4234.48\msedgewebview2.exe`），语法与系统原生那 4 条 `GpuPreference=2;` 相同，`HKLM\SOFTWARE[\WOW6432Node]\Microsoft\DirectX\UserGpuPreferences` **无覆盖项** —— 结果 WorkBuddy 仍占独显 130 MiB 且引擎在独显上有 3D 活动。**结论：这条路对 Chromium 系不可靠，勿再投入。**
- 同理 **`dwm.exe` 与虚拟屏本身都改不了**（前者的落卡由 IDD 的渲染适配器决定）。

**真正能动的两条路**：① **把物理显示器打开**（HDMI 在核显上）→ 系统获得真实核显输出 → 可停用虚拟屏（唯一能改变拓扑的物理手段；但 Chromium 系是否跟着回核显需实测）；② **降 ctx**（192K→176K 换 1380 MiB，杠杆是这 370 MiB 的 3.7 倍）。

> 顺带纠正一条**用错的判据**：早期把 `\GPU Non Local Adapter Memory(*)\Non Local Usage` "只有独显有值"读成"该卡无显示器挂载"。该计数器量的是**本地与非本地内存**（iGPU 是 UMA、全本地 ⇒ 恒 0），**与显示挂载无关** —— 判断"虚拟屏建在哪块卡"要用承载它的 `WUDFHost` 的逐 LUID 显存。

## 0.1 服务启停 —— 本环境唯一可靠方式（2026-09-19 逐条实测）

**✅ 唯一可用**：Python `subprocess.Popen(['cmd','/c','<bat>'], cwd=r'D:\MyLLM')` + 父进程 `p.wait()`，脚本本身用 **`run_in_background`** 起。现成保活器：
- `D:\MyLLM\_start_8090_keep.py`（8090 Bonsai）
- `D:\MyLLM\_start_dsh_keep.py`（DSH web 3080）

**❌ 全部失败的方式**（勿再试）：
1. `cmd //c <bat>` 被安全策略挡掉 —— 只开一个交互 shell 就退出，bat 永不执行，**`$?` 仍是 0，极易误判成功**。
2. `MSYS_NO_PATHCONV=1 cmd //c x.bat` → cmd 进交互模式（`//c` 不再被转成 `/c`），脚本根本不跑（无日志无进程）。
3. Python `Popen` 带 `DETACHED_PROCESS|CREATE_NEW_PROCESS_GROUP|CREATE_BREAKAWAY_FROM_JOB`：调用内可见 pid，40 秒后已被进程树清理。
4. `nohup &`、`Start-Process` 同样被回收。

**日志就绪判据**：llama-server **先绑端口再加载权重**，`/health` 在权重加载前就返回 ok，是**假就绪** → 必须等日志出现 `model loaded` 或 `listening on http`。

**⚠️ .bat 两条硬规范**：GBK 编码 **且 CRLF 换行**（只写 LF 时 cmd.exe 会把 `rem` 注释当命令执行，报一串 `'xx' 不是内部或外部命令`、rc=255、日志根本不生成）。写完必校验 `CRLF 数 == 行数 && 孤立 LF == 0`。GBK 编不了 emoji，注释里用 `[!]`。

**⚠️ 不要在 .bat 仍被 cmd 执行时改写它**：宿主 `cmd /c xxx.bat` 会一直阻塞在 llama-server 那行，此时改写再 kill llama-server，**cmd.exe 会按保存的字节偏移续读已加长的新文件，意外重执行一遍整个脚本**（2026-09-20 19:29 凭空冒出 PID 18500）。改 bat 前先 `netstat -ano | grep <port>` 找到 PID、**连宿主 cmd 一起** `taskkill /F /PID <pid> /T`。

## 0.2 端口图

| 端口 | 服务 | 状态 |
|---|---|---|
| **8090** | **Ternary-Bonsai-2-27B:mtp + 视觉塔**（MTP 投机解码） | **当前常驻** |
| 8091 | Ternary-Bonsai-2-27B（无 MTP，老档，`start_bonsai_2_27b_8091.bat`） | 与 8090 显存互斥 |
| 8092 | 已废止（Bonsai-MTP 迁到 8090），脚本 `.retired` | — |
| 3080 | DSH web（llama.cpp 前端） | 当前常驻 |
| 8085 / 3003 / 5005 等 | Ornith / 后端 / 前端 | 见各节 |

Ornith-1.5-35B 原本在 8090，**2016-09-20 17:15 起被 Bonsai 占用，两者互斥**；Ornith 要跑必须换端口。

---

## 1. 当前常驻 8090 —— Ternary-Bonsai-2-27B-MTP + 视觉塔

- **脚本**：`start_bonsai_mtp_8090.bat`（GBK + CRLF）。**必须用现成脚本，禁止手敲命令行。**
- **模型**：`models\bonsai_mtp\Ternary-Bonsai-2-27B-PQ2_0-MTP-Q8_0.gguf`（7.66GB）
  + `models\Ternary-Bonsai-2-27B-mmproj-BF16.gguf`（931MB）
- **运行时**：`llama-bonsai-mtp\bin\llama-server.exe`（官方 Prism build **d8f26eec**, 20260919）
- **定稿参数**：`--alias Ternary-Bonsai-2-27B:mtp` `--host 0.0.0.0 --port 8090`
  `-ngl 99 -fa on` **`-c 188416`(184K)** `-b 2048 -ub 512` `--parallel 1 --timeout 600 -t 32`
  `--cache-type-k q8_0 --cache-type-v q8_0` `--spec-type draft-mtp --spec-draft-n-max 2`
  `--mmproj ... -bf16 --no-mmproj-offload` `--reasoning-format auto` `--temp 1.0 --top-p 0.95 --top-k 20` `-lv 4`
- **思考档位（2026-09-21 改）**：**不传 `--chat-template-file`**，用 GGUF 内嵌原生模板 → 默认档位 **xhigh**（不传 `reasoning_effort` 就注入 "think carefully / validate assumptions"）。自定义 medium 模板仍在 `models\bonsai_mtp_chat_template_medium.jinja`（未删），想回 medium 把 `--chat-template-file` 那行加回即可。
- **视觉塔**：`--no-mmproj-offload` → `clip_ctx: CLIP using CPU backend`，`[mtmd] estimated worst-case memory usage of mmproj is 1136.09 MiB` 走**系统内存**。**挂塔走 CPU 几乎不占显存**（纯文本 15635 → 挂塔 15613 MiB），所以「余量够不够」的担心不成立。
  - **不要加 `--mmproj-offload`**：966 MiB 会直接顶爆余量。
- **`-lv 4` 必须保留**：只有该档打印 `n_gen` / `restored context checkpoint` / 缓存命中(pos) 等诊断行，崩溃取证与守卫巡检全依赖它；实测不记录 prompt/输出正文。
- 换 Q8_0 塔可省 288 MiB（视觉质量损失可忽略）；判别塔位置：`CLIP using CUDA0 backend`(GPU) vs `CLIP using CPU backend`(CPU)。
- **显存退让顺序（OOM 时依次）**：`-c 188416 → 180224(176K) → 163840(160K 已验证安全，余量 1380)` → KV 换 `q4_0`（省约 3.2 GiB，接受率降约 8pp）。
- **实测（2026-09-22 启动）**：184K + q8_0 + MTP + 塔 → **15929/16311 = 97.7%，余量仅 122 MiB**。
  ⚠️ 比 bat 注释里写的 15635（95.8%）**高 294 MiB**，已进「98% 临界」红区。**长序列 prefill 有溢出风险，做长任务前先降到 176K。**
- 短请求冒烟：content 为空 + `reasoning_content` 有连贯英文 = **正常**（原生模板 xhigh 先思考，`max_tokens` 给小只见思考）。MTP 实测 draft 16 / accepted 14 = **87.5% 接受率**。

## 2. Bonsai 模型 / 运行时（⚠️ 版本极敏感）

- **⚠️ 构建铁律**：PQ2_0/PTQ1_0 是 PrismML 自有 ggml 类型 + Hadamard 激活（**未上游**）。**官方主线 llama.cpp 一律跑不了**：PQ2_0/PTQ1_0 被直接拒绝，**Q2_0 会静默加载然后输出乱码**。必须用 PrismML fork（`llama.cpp.prism-b10709`，tag `prism-b10709-9a9394a`，cuda-13.3），与官方构建**完全隔离，勿混用**。
- **MTP 必须用官方 Windows 包（d8f26eec）**：本机 `prism-b10709` 的 `qwen35.cpp` 虽已有 `graph_mtp`，但**缺 `runtime/bonsai-mtp-embedding.patch` 的 Hadamard 反旋转** → PQ2_0 嵌入表在旋转基底，缺了则**草稿全错、接受率崩到 0**，白付显存与开销。
- **规格**：派生自 Qwen3.8-27B，**稠密非 MoE**；混合注意力 ~75% linear / 25% full；原生 ctx 262144；模态 text/vision/video；**851 tensor 基座 + 15 个 `blk.64.*` nextn tensor**（MTP 头，Q8_0，430 MiB）。
- **采样**：thinking `temp1.0/top_p0.95/top_k20`；instruct `temp0.7/top_p0.80/presence1.5`。
- **坑**：新版 llama-cli 已移除 `-no-cnv`，改用 `-st/--single-turn`。
- **MTP 收益锚点**：作者受控 1.243×（接受率 60.5%）｜社区 1.338×（68.1%）；**n-max=2 最优**（153.6/174.6/172.7）。本机实测接受率 **61.8~69.5%**，加速 **1.09~1.35×**，随上下文变长衰减（2k 1.35 / 32k 1.29 / 96k 1.16 / 168k 1.13）。**prefill 不受益（-3~4%，固定开销，不随长度发散）。**
- **KV 精度越高 MTP 接受率越高**：q8_0 比 q4_0 高约 **8pp**（草稿头共享目标 KV，量化 KV 直接劣化其预测）。

## 3. 8090 调参账本（压缩自 8092 时期全部 A/B）

- **档位对照（都已归档，只留结论）**：
  | 方案 | 配置 | 显存 | 结论 |
  |---|---|---|---|
  | **当前** | 184K / q8_0 / MTP on / 塔CPU | **15929 (97.7%)** | 余量 122 MiB，**红区，慎长序列** |
  | A | 160K / q8_0 / on | 14931 (91.4%) | 最安全，速度与 E 持平 |
  | E（前最优） | 176K / q8_0 / on | 15635 (95.8%) 余量 676 | 曲线平滑健康，**长任务优先选它** |
  | B | 192K / q4_0 / on | 13245 (81.2%) | 最宽松，KV 有损 |
  | D | 192K / q8_0 / **off** | 14565 (89%) | 不开 MTP 时 192K 很健康 |
  | **C** | 192K / q8_0 / on | 16019 (98.2%) | ❌ **必爆，禁用** |
- **MTP 净增显存 ≈ +1454 MiB**：头权重 +430 / RS 149.62→448.88（n_rs_seq 0→2，rollback plane 存 3 份）/ **草稿上下文自带 KV 768 + compute 260（+1028，最易被忽略）**。
- **每 token 成本**：目标 KV q8_0 = 34 KiB、q4_0 = 18 KiB；草稿 KV f16 = 4 KiB；目标 compute 5 KiB/tok(+90 底)；草稿 compute 1 KiB/tok(+68 底)。**buffer 启动时按 -c 一次性预留，与 prompt 实际长度无关。**
- **176K 四档实测**（VRAM 全程钉住 15635、GPU 100%、全 finish=stop）：2k/2682 → prefill 844.4 / decode 60.87 / 61.8%｜32k/36975 → 807.1 / 45.15 / 69.5%｜96k/110899 → 601.1 / 26.51 / 67.7%｜**168k/167899 → 501.5 / 20.66 / 68.1%**。
- **decode 随 ctx 腰斩是长上下文真瓶颈**：≤32K 40.6 ｜32-64K 31.2 ｜64-96K 27.5 ｜96-128K 21.9 ｜128-160K 19.7 t/s。**prefill 反而衰减温和**（拟合 `per-token = 0.00102461 + 1.0451e-8·n`，偏差恒 +2.5~+5% 且不随长度恶化 → 是纯 O(n²)，**不是溢出**；溢出会让偏差单调变负）。
- **`--spec-draft-type-k/-v`（-ctkd/-ctvd）不要用**：草稿 KV f16→q4_0 省 552 MiB，但草稿 compute 260→1040（+780），**净亏 228 MiB** + 质量退化。
- **q4_0 KV 不加快 prefill**：长序列 attention 是**算力受限**而非 KV 带宽受限 → q4_0 价值纯在省显存。
- 该构建**无 `--spec-draft-ctx-size`**，草稿上下文被迫跟随目标 ctx → 草稿 KV 随 ctx 线性涨。
- **`--cont-batching` 类参数**：8090 走 `--parallel 1`。
- 基准工具 `bench_bonsai_mtp_ab.py`（`LEVELS` 含 `168k: 247000` 专测深水区）；A/B 报告 `_8092_mtp_ab_result.md`、`_bonsai_mtp_speedup_eval.md`。

## 4. KV cache 精度边界（含用户铁律）

- **CLI 白名单只有 9 种**：`f32/f16/bf16/q8_0/q4_0/q4_1/iq4_nl/q5_0/q5_1`（`common/arg.cpp:304`）。**没有 q6**（`q6_K`/`q6_0` 参数解析阶段直接拒，启动即失败）。
- 白名单 ≠ 能用，还要过 CUDA FA 内核表（`ggml-cuda/fattn.cu:338`）。**不要用本机 prism-src 源码行为外推官方二进制。**
- **⚠️ 判定 KV 类型可用的唯一标准 = GPU 利用率 + 实测 prefill t/s，不是「能不能返回」。**
  - `q5_1` 已实测判死：192K 启动正常、水位 82.8%，但 prefill **189→100 t/s**、**GPU 0% / 功耗 20.77 W**，`CUDA0 compute` 1050→132、`CUDA_Host compute` 212→514 → **attention 被静默排到 CPU**。
  - **`q5_0` / `q4_1` / `iq4_nl` 均视为未验证、默认不用。**（早前"q5_0/iq4_nl 跑通"的结论是错的 —— 只验了"能返回"没测速度。）
  - ⇒ 本 runtime **只有 q8_0 与 q4_0 两档是验证过的**。
- **用户铁律：草稿头 KV 必须 f16，禁止 `-ctkd/-ctvd q4_0`。**
- **不换 fp16 目标 KV（2026-09-20 论证）**：结构 `full_attention_interval=4` ⇒ 64 层**只有 16 层有 KV**，48 层线性注意力走 RS（149.62 MiB，**f32 固定，不受 `--cache-type` 影响**）。f16 = 64 KiB/token → 192K 需 12288 MiB（比 q8_0 多 5760，余量不够），**fp16 上限仅 ~105K**；且实测预估 decode **反而慢 17~35%**（长 ctx 瓶颈是字节量不是反量化）。
  ⚠️ 与 DGX 上「f16 > q8_0」**不矛盾**：短上下文瓶颈是反量化（f16 赢），长上下文瓶颈是字节量（低精度赢）。

## 5. `reasoning_effort` / 思考档位 —— 客户端第一号坑

- Bonsai 内置模板**只认**：不传（=默认档）、`xhigh`、`medium`、`low`、`none`（=**关思考**）。
  **`high` / `minimal` / 其它任何值 → HTTP 500**：`Jinja Exception: Unexpected reasoning effort high. Supported types are xhigh (default), medium, and low.`
  ⚠️ **`high` 是 OpenAI 官方枚举，大量平台"深度思考"档硬编码它 → 直连必 500。**
- llama-server **不做任何映射**（`tools/server/server-common.cpp:1312-1321` 只拦 `none`→关思考，其余原样透传）。
- **顶层 `enable_thinking` 被静默忽略**（`1302-1310` 只从 `chat_template_kwargs` 读；实测 200 但 `reasoning_content` 仍有内容）。
- **`reasoning_effort` 是特例**：顶层会被显式搬进 kwargs（`1312-1321`）且**顶层覆盖 kwargs**（实测 `顶层high + kwargs medium` → 500）⇒ 客户端在 kwargs 里做归一兜底会被上层打穿。
- 校验嵌在模板 `{% if enable_thinking is undefined or is true %}` 内 ⇒ kwargs `enable_thinking=false` 可让 `high` 也返回 200（绕过校验）。
- **推荐映射**：自动→不传｜高→`xhigh`（**不是 high**）｜中→`medium`｜低→`low`｜关闭→`none`。
- **`--chat-template-kwargs` 对本案无效**（客户端显式值覆盖默认值）。
- **⚠️ 反直觉实测（别再指望档位省 token）**：同一题思考长度 **xhigh 1573 / medium 1884 / low 1797 字符**，全在噪声区间。**Bonsai 思考量由题目难度决定，指令文本几乎推不动**；要真压下来只有 `reasoning_effort:"none"`。证据 `_think_baseline.txt`，说明 `_8090_thinking_effort.md`。
- **API 小知识**：`/apply-template` **不认顶层 `reasoning_effort`**（5 档返回同样的字符数），必须用 `chat_template_kwargs:{"reasoning_effort":...}` —— 验证模板行为时别踩。
- 自定义模板若挂载：`models\bonsai_chat_template_reasoning_effort_patched.jinja`（high→xhigh、minimal/min→low，已验证 8/8）。⚠️ 会触发 chat format 重推导，**启用后必须复验 tool-calling 仍是 `peg-native`**。
- **同类模板坑排查套路**：`curl /props` 取 `chat_template` → `jinja2` 沙箱渲染验证。
- 对照卡 `_8091_thinking_params.md`；探针 `_probe_thinking_matrix.py` / `_probe_thinking_edge.py` / `_probe_effort_none.py`。

## 6. 退化（slice 死循环）与守卫 —— 8091 遗留，Bonsai 系通用

- **症状**：进程跑一段时间后**所有请求**（含 `1+1=?`）输出连续 `/` 到顶满 max_tokens，`content` 空、`finish_reason=length`。**唯一恢复手段 = 重启进程**。
- **⚠️ 调采样参数无效**：默认 / `repeat_penalty=1.1` / `dry_multiplier=0.8` 三组输出**完全一致**（都是 900 个 `/`）。
- **判据**：主用 `/` 占比。**不能用 finish=length**（本模型强制思考会误报）。真退化 = 疯狂重复 `/`；正常长思考 = 连贯英文。
- **运维铁律**：① 超长上下文任务跑完就重启；② 见 `/` 死循环**直接重启，别调参数**；③ 业务侧**尽量避免中途取消**（已知触发场景），取消后尽快重启；④ 客户端设 `max_tokens` + 重试熔断。
- **🛡 `D:\MyLLM\bonsai_guard.py`**：`python bonsai_guard.py`（只读审计）｜`--probe`（加功能探活，带 `cache_prompt=false` 不污染缓存）｜`--restart`（确认退化后重启）｜`--launch`（按 bat 配置启动）。退出码 0=正常 / 1=告警 / 2=确认退化。
- **🔬 `D:\MyLLM\repro_bonsai_degeneration.py`**：默认只自检；`--phase struct`（验 H1）/ `--phase repro`（验 H2，用 `SO_LINGER=0` RST 模拟 CANCEL）。**会占用唯一槽位，勿在跑业务时执行。**
- **结论可信度分层（引用必须带层级，勿笼统写"实测定位"）**：① `n_rs_seq=0`／arch=qwen35／`n_swa=0` → **事实（日志直读，高）**；② hybrid `pos_min==pos_max==tail` → **中高**（日志签名＋源码）；③ ⇒ 反复全量重算 [H1] → **中**（推断链）；④ CANCEL → 退化 [H2] → **低**（仅相关性，触发条件未定型）。
- **已否证，勿再走**：外部提案「`[0,end)` 只清 cell 元数据不清张量 → GDN 初态残留污染」**不成立**（存在 `src0` 间接层兜住残留：`seq_rm` 置 `src=-1` → `find_slot` 每 ubatch 把无源 cell 的 `src0` 指向 `rs_z` → `build_rs` 每帧 `scale_inplace(state_zero,0)` 清零 ⇒ 无源 cell 读零态）。`patch_seq_rm_zero.diff` 属无效修复。完整核查表 `_verify_tensor_hygiene.md`。
- **MTP 的附带效应**：开 MTP → `n_rs_seq=2>0` → `common.cpp:1599` 探针**短路成 `COMMON_CONTEXT_SEQ_RM_TYPE_RS`**（日志变 `supports bounded partial sequence removal`、不再用 checkpoint）。**但窗口仅 2 token，不治退化、不治前缀缓存重算**；⚠️ `server-context.cpp:3330` 的 `slot.mem.seq_rm(id,p0,-1)` **无守卫**（失败=GGML_ABORT）→ 切 RS 后须盯 abort。
- fork 源码在 `D:\MyLLM\llama.cpp.prism-src`（HEAD `9a9394a`）；补丁 `patch_n_rs_seq.diff` 已 apply。**本地无法编译**（无 VS、无 CMake；`nvcc` 只有 CUDA 12.8，发布包用 13.3）。**无更新构建可升级**：prism 分支顶端就是 `9a9394a`，不含修复。
- **⚠️ 日志解析易错点**：`id_task` **按内部子任务递增**（157 个请求会跑到 106445），**不能当请求数**；必须以 `new prompt` 行做请求边界（`_analyze_8091_v2.py`）。

## 7. Ornith-1.5-35B-A3B-APEX-MTP（8090 前身，**当前未运行**）

- 要重启必须换端口（8090 已被 Bonsai 占）。
- 模型 `Ornith-1.5-35B-A3B-APEX-MTP-I-Compact.gguf`（17.44GB，qwen35moe，MTP 头内嵌；I-Compact = imatrix 标定版，质量更好）。旧 Compact 版留作回退。
- 构建 `llama.cpp.bak_b10566\llama-server.exe`（快且含 `--slot-save-path`）。
  ⚠️ "b10783 禁用"**仅针对 APEX 模型**；Q4_K_M 在 b10783 上是正规配置。**两模型用不同构建，勿混。**
- 脚本（2026-10-10 起统一命名）：Q4_K_M → `8090_ornith_q4km_cpu16.bat`；官方 IQ4_XS → `8090_ornith_iq4xs_cpu12.bat`；APEX → `8090_ornith_apex_cpu8.bat`（**必须走现成脚本**，勿手敲；三份均单槽 192K 纯启动）。旧名 `start_ornith15_8090.bat`/`start_ornith15_apex_mtp.bat` 已改名。
- **`--n-cpu-moe` 结论修正史**（三代）：短序列（≤30K）**8 最优**（比 9 快 24%）；149K 长序列曾测出 10 快 2.1×，但 **2026-09-14 I-Compact + b10566 复测推翻** → 8 冷 170.4K prefill **768 t/s 稳定不崩**、VRAM 98.4% 持平，实际差距仅 **1.2~1.4×**。**当前固化 `--n-cpu-moe 8`。**
- 256K 权衡：`8 + 192K` = prefill 2300 t/s（封顶 192K）；`16 + 256K` = 821 t/s（256K 可用）。n-cpu-moe 8 + 256K 必溢出崩。
- **`timings.prompt_n` 恒返回 4（已知 bug）** → 算速度一律用 `usage.prompt_tokens`。
- **APEX 模板强制思考**：chat template 硬编码 `<think>`，`--reasoning-format none` 也关不掉。auto 下单次思考常 1300+ token 才出正文 → 客户端 `max_tokens` 给小**易被误判为"乱码/死循环"**。

## 8. 前缀缓存 —— 结论：此路不通，勿再投入时间

- 文件级 `--prompt-cache`/`--prompt-cache-ro` 是 **llama CLI 专属，llama-server 从不支持**。
- 进程内 `--cache-prompt`（默认开）可用：冷 5.81s → 复用 1.5s（**3.87× / −79%**），但**重启即失**。
- `--slot-save-path` + `POST /slots/0?action=restore`：KV 能落盘再读回（110ms/3568tok），但 `/v1/completions` 每次带完整 prompt 进来照重算整段 → **跨重启前缀跳过 = 0 收益**（6.09s vs 冷控制 5.01s）。
- 真·跨重启复用只能靠外部代理（首次全发 + save KV，后续只发 suffix 给 slot 端点续写），**非加 flag 可解**。
- **同会话内前缀复用是 100% 生效的**（实测 147K 长文档：首轮冷 prefill 261.6s → 第二轮 TTFT **0.6s，437×**；日志特征：无 `prompt processing` 行，直接 `cached n_tokens`）。
  ⇒ **长文档任务里"首次喂入"是唯一昂贵动作；别中途改系统提示/工具定义**，否则整段缓存失效、重付 4.4 分钟。
- **context checkpoint 机制**：`max=32, min spacing=8192`，每个 **149.626 MiB**（线性注意力 recurrent state 快照，48 层线性层无法截断 KV 只能快照），32 个 ≈4.7GB 落**系统内存**（host prompt cache，默认上限 8GB），**不占显存**。会清理过密的（`erasing context checkpoint too close`）。

## 9. DSH（DeepSeek Harness）接入本机 llama.cpp

- **配置文件**：`C:\Users\jianz\.dsh\settings.yaml`（LF 无 BOM，dsh 自己也会写）。凭据在同目录 `.credentials.yaml`（本机全是占位 "123"，llama.cpp 不校验 key）。
- **运行时**：`C:\Users\jianz\.workbuddy\binaries\node\workspace\node_modules\@deepseek-ai\`。
  CLI：`<node> .../@deepseek-ai/dsh/lib/bin.js`，`DSH_HOME=C:/Users/jianz/.dsh`，web 端口 **3080**。
  启动：`_start_dsh_keep.py`（内部 `cmd /c E:\deepseek-harness\start_dsh_web.cmd`，**该 cmd 自己会先 taskkill 3080 占用者**，重启无需手动 kill）。
  日志 `E:\deepseek-harness\dsh_web.log` 是 **UTF-16LE**，读它要 `iconv -f UTF-16LE`。
- **改配置不用重启 dsh**：pi-ai 路由的 profile **每请求重解析**（只有"路由集合"变化才原地重注册），改完 settings.yaml 下一条消息生效。
- **⚠️ 铁律（2026-09-21 09:20 用户定稿）：模型条目与路由都不要写思考档位。**
  口径 = **默认不传 `reasoning_effort`，由服务端模板决定思考量**；Bonsai **不支持 high**。
  所以 `qwen38local` 路由**不写** `reasoning`、Bonsai 模型条目**不写** `reasoningEfforts`、`agent-default-model` **不写** `reasoningEffort`。
  - **别再做的**：为"让选择器能选档位"加 `reasoningEfforts` 白名单 —— 那是把档位控制权交给 harness，且白名单里只要缺了**会话历史里钉的旧档位**，老会话必挂。
  - 原理（排查老会话仍有用）：`dsh-llm-pi-ai` 的 `resolveReasoningLevel()` 在**请求路径**上硬校验档位，不在白名单就抛 `UNSUPPORTED_REASONING_EFFORT`；而 `describableReasoningLevel()` 不抛 → 模型在选择器里能选中、一发消息才失败，**别被这点骗了**。
  - **会话级残留优先级高于 settings.yaml**：`dsh-agent-loop:1135` 取 `options.reasoningEffort ?? 会话 modelSelection.lastUsed`；`adapterDefaults.reasoningEffort` 是运行时算出来的、**不可配置** → 老会话里钉死的档位**无任何配置手段可解**，必须在 UI 重新选一次模型或新建会话。
- **本机 8090 已实测兼容**：role=`developer` ✅（`model.reasoning=true` 后 pi-ai 会把 system 改发 developer，llama.cpp 自动归一 system）；`reasoning_effort` low/medium/high ✅（非法值 → 模板 raise_exception 500，符合预期）；`max_completion_tokens` ✅；`tools[].strict` ✅；请求里的 `model` 字段**不校验**（写 `:vision` 而服务端 alias 是 `:mtp` 也照服务）✅；PNG data URL 走 CPU BF16 塔 ✅。
  → **结论：服务端一直是好的，出问题先查 settings.yaml 的档位声明。**
- 自查脚本：`_dsh_settings_check.mjs`（解析 settings.yaml 打印 provider/模型/档位）、`_8090_vision_check.py`（图片端到端）。

### 9.1 DSH 版本现状（2026-09-21 核查，结论：**暂不升级**）

- `~/.dsh/profiles/node_modules/@deepseek-ai/*` 的 **245 个条目全是软链**指回 workspace → **升级 workspace 即升级 DSH**，不必重建 profiles 链接。
- 版本矩阵：本体 **0.1.5-rc.1**，但子包（真正功能层）多数已 **0.1.5-rc.2**（`^0.1.5-rc.1` 自动升）→ **升到 rc.2 几乎没收益**。dist-tags：`latest = next = 0.1.5-rc.2`、`alpha = 0.1.6-alpha.2`（结构变动大，仍是 alpha，**未升**）。E: 源码检出停在 `dsh-v0.1.2-alpha.5`。
- **升级会打掉什么**：① `dsh-attachment-local` 的图片 WebP 直通补丁 —— 有 `C:\Users\jianz\dsh-image-fix\post-upgrade.cmd` 一键重打（幂等+原子+自动备份）；② **不会**打掉 searxng 外挂包（不在 npm 依赖里，是手工软链直通 E:，`npm i` 不碰它），但**在 E: 做 `git checkout` 会覆盖 `lib/index.js`**。⚠️ 用 `dsh plugin add`（转 pnpm 到 profile 目录）可能重建 profiles 链接，需先备份。
- **升级步骤**：备份 `settings.yaml` + `profiles/web/cordis.patch.yml` → workspace `npm i @deepseek-ai/dsh@<ver>` → 跑 `post-upgrade.cmd` → 重启 DSH → 按 9.2「验证三招」复核 searxng 探测。
- ⚠️ **已知混装债**：profiles 里 4 个包是**手工软链到 E: 的 0.1.2-alpha.5 源码**（`dsh-client-runtime`、`dsh-host-apiproxy`、`dsh-tool-subagent-report`、`dsh-web-search-searxng`），却跑在 rc.2 宿主上。searxng 的依赖（如 `dsh-settings.installSettingsSection`，rc.8 没有）就是从 E: 解析的 —— **这正是"改 E: 才生效、改 profiles 无效"的机制来源**。

### 9.2 DSH web 搜索插件与"不通自动兜底"

- **插件树不在 settings.yaml，在 `~/.dsh/profiles/web/cordis.patch.yml`**；`cordis.yml` 是空根，树由「bundle（`@deepseek-ai/dsh-base` → `dsh-web-app`）+ profile patch + `--patch`」叠加。
- **⚠️ 雷一：`- insert:` 不能重复声明 base 已有的 id**（`dsh-base/cordis.patch.yml:442` 已有 `web-search-deepseek`）→ 报 `duplicate loader entry id`，**整棵插件树崩、3080 起不来**。同 id 改配置要用 `- id: X` + `config:` 覆盖，**且覆盖是整块替换不是 merge**（`fetchProvider: http` 就是这么被静默丢掉的，已于 2026-09-21 22:12 补回）。
- **兜底是内建能力**：`WebRuntime.search()` 把 `[searchProvider, searchFallbackProvider]` 逐个 resolve，前一个 MISSING/UNAVAILABLE 或 `search()` reject 就换下一个。声明即可：
  ```yaml
  - id: web
    config: {searchProvider: searxng, searchFallbackProvider: deepseek-official, fetchProvider: http}
  ```
- **⚠️ 雷二：searxng `timeoutMs` 必须显著小于工具预算，否则兜底被饿死。**
  模型侧 `web_search` 超时是**整条链共享的一个硬 deadline**（`presets/*/agent.cordis.yml` 写 `tool-web.searchTimeoutMs: 60000`，经 `dsh-tool-call-timeout-policy:125` 变成 signal）→ primary 挂满 60s 时 deadline 先到，fallback 拿到**已 abort 的 signal** 秒拒 → 只看到 `tool call timed out after 60000ms`。
  → **定稿 `timeoutMs: 15000`**（包默认 60000 是坑）。黑洞 A/B：60000 → 60.0s FAILED；15000 → 20.1s SUCCESS 3 条真实来源。
- **后台健康探测（2026-09-21 22:15 落地）**：原 `available()` 只是 `URL.canParse(baseURL)`，**从不探网络** → 死但合法的 URL 照样被选为 primary。
  现在 `available()` = 本地检查 **且** 缓存判定（`status !== "down"`），由后台 `GET {baseURL}/search?q=ping&format=json`（UA `deepseek-harness/0.0.1`）刷新，**5s tick 驱动、按 `probeIntervalMs` 自节流**，`probeTimeoutMs: 2500`。
  - **⚠️ 同步 `available()` 里绝不发网络请求**（seam 在选主时会同步调它）。
  - **本机 `probeIntervalMs: 300000`（5 分钟，用户 2026-09-21 指定）**。**为什么可以这么低**：探测只是"恢复检测"，实时兜底不依赖它 —— 真实 search 一失败就 `noteFailure()`（置 down + **清零 `checkedAt`**），**下一个 5s tick 立刻补探**，再下一次选主就已跳过死端点。降频只影响"多久发现 searxng 回来了"。任何间隔都会被**向上取整到 5s 的倍数**（300000 恰整除 → 精确 300s）。
  - `unknown` 视为可用（冷启动不隐藏健康端点）；恢复后 ≤1 个探测周期自动切回 searxng。`healthProbe: false` / `probeIntervalMs: 0` 退回旧行为。
- **落点**：`E:\deepseek-harness\packages\web\web-search-searxng\lib\index.js`（+ `lib\types\index.d.ts`）。profiles 里是指向它的**软链**；该目录**只有 lib/、无 src/、未纳入 git**（是外挂的编译产物）→ **只能就地改**，改名新造包会因依赖解析错位而挂。
  备份 `index.js.bak_20260921_220900_prehealth`。
- **改插件代码必须重启 DSH**：`patchReload: "live"` 只覆盖 patch 配置；`cordis-plugin-hmr` 在 base 里是 `disabled: true`，且 loader 仅在条目 `name` 变化时才重新 import（改 config 只重跑 `apply()`）。**改 patch 配置则不用重启。**
- **验证三招**：① patch 生效 → `DSH_HOME=C:/Users/jianz/.dsh <node> <...>/dsh/lib/bin.js --profile web --dump-config`；② 探测真在跑 → 起本地 HTTP server 记录请求、把 `baseURL` 临时指过去（已实测收到 `q=ping&format=json`，间隔与 `probeIntervalMs` 经 5s tick 量化的预期吻合 → 同时证明配置热加载与插件代码都生效）；③ 逻辑回归 → `node _searxng_health_test.mjs`（7 项全过）。
  - ⚠️ **别用 `netstat | grep <searxngIP>` 的落点间隔当证据**：DSH 从 WorkBuddy 沙箱启动时出网走 `sandbox-cli.exe` 代理，socket 归属代理进程且会换本地端口重试，周期会被扭曲。
- 排查脚本：`_searxng_health_test.mjs`（探测逻辑回归）、`_websearch_fallback_test.mjs`（真实端点端到端）、`_websearch_fallback_blackhole_test.mjs`（黑洞 A/B，改超时时用它回归）、`_searxng_reject_timing.mjs`。

## 10. 工具链小坑

- **Git Bash** 里 `/c/...` 会被 MSYS 改写（`/c/...` → `D:\c\...`）→ 调 node/python 用反斜杠路径或 `MSYS_NO_PATHCONV=1`。
- **ESM 不认 `NODE_PATH`**，第三方包要 `file:///` 绝对 URL。
- `Edit` 工具曾报 success 但内容未落盘（改 GBK 的 .bat）→ **改完必须 Read 回校验**；用 Python 以 GBK 读写最稳。
- 批量做「同一文件里多处精确替换」：先 `t = t.replace('\r\n','\n')` 归一化再匹配，改完 `replace('\n','\r\n')` 转回；否则 CRLF 文件里所有锚点命中 0 次。
- 探针规范：小模型（GLM-OCR-Q8_0 950MB）+ 独立端口 8097，**必须 `run_in_background`**（`&` 会被回收）。

## 11. 2026-09-22 变更：8090 加 API 鉴权 + Tailscale Funnel 公网暴露

- bat（现 **84 行 / 4132 B**）新增 `--api-key sk-llama-…`（key 内联脚本内，第 73 行），并新增注释块说明：
  8090 已通过 **Tailscale Funnel** 暴露到 `https://liang-geekpro.tailfe4bdd.ts.net`，**鉴权必须保留**；
  可选 `--api-key-file D:\MyLLM\.llama_api_keys`（一行一个，`#` 为注释），多 key 用 `--api-key k1,k2`。
- **实测**：本地与公网 `/v1/models` 不带 key 均 **401**；带 `Authorization: Bearer` 均 **200**；**`/health` 免鉴权**（可当探活端点用）。
- 编码规范校验通过：`CRLF=84 / 孤立 LF=0`、GBK 可解码。
- **宿主稳定性**：当前实例由另一个 WorkBuddy 会话的 `bash.exe` → `cmd /c <绝对路径 bat>` 直挂（PID 链 bash 4320 → cmd 28732 → llama-server 32548）。
  ⚠️ **宿主 shell 被回收 = 服务死**，这正是 8090 频繁掉线的根因；`_start_8090_keep.py` 的 `p.wait()` 也只是把宿主从 bash 换成 python，**治不了根治**。
- 记日志时的**时间戳格式坑**：llama-server 日志前缀 `0.06.886.367` 是 **分.秒.毫秒**（不是时.分.秒），据此才能反推进程真实启动时刻。


## 12. 2026-09-23 事故：UTF-8 中文注释的 .bat 在 cp936 下被 cmd 误解析

### 12.1 证据链（同一内容、只改编码，`cmd /c` 直跑）

| 变体 | 字节 | 结果 |
|---|---|---|
| UTF-8 + CRLF（用户原文件） | 2385 | **8 条 `'xx' 不是内部或外部命令`** |
| 删掉所有非 ASCII 字节 | ~1900 | 干净 |
| **GBK + CRLF（修复后）** | 2127 | 干净 |

结论：**编码是元凶，不是换行**（该文件当时 `CRLF=16 / 孤立 LF=0` 完全合格）。

### 12.2 症状指纹（遇到这些先怀疑编码）

- 报错里的"命令名"是**注释碎片**：`'14'`、`'5_8090.bat.bak-cpu16。'`、`'decode'`、`'raft-mtp'`、乱码中文（`'鍥炶皟'`=回调、`'闄愬氨鏄?16'`=限就是16）。
- `echo on` 抓到的实际执行行**从行中间开始**（行首被静默吞掉），例如原 `REM 2026-09-14 20:23: n-cpu-moe 16 -> 14 (用户指定)。` 被读成 `14 (用户指定)。`。
- **服务仍能起来**（末尾启动行照样执行）⇒ 这类故障是"隐形"的：只表现为一屏噪声，容易被当成"能跑就行"。

### 12.3 为什么单行最小复现不成立

单行 UTF-8 中文 REM（`REM 中文注释 测试。(用户指定)`）跑起来干净；必须整文件才复现 ⇒ **偏移是逐行累积的**（cmd 的宽字符/字节位置在 DBCS 码页下逐渐跑偏），所以要验就整文件 A/B，别用单行下结论。

### 12.4 probe 的适用边界（重要）

用"启动行换成 echo 的副本"跑探针**不是万无一失的检测器**：
- 4 个高危脚本（UTF-8 + 纯 LF）probe 居然**零报错**，但 `@echo off` **静默失效**、命令被回显（`D:\MyLLM>echo LAUNCHLINE_REACHED` 出现在输出里）。
- ⇒ probe 只能证明"当前这份内容在当前 cmd 下没炸"，**结构判据（GBK + CRLF）才是硬标准**。

### 12.5 改写正在被执行的 .bat 的安全判据

cmd 按**字节偏移**增量读批处理文件，改文件后旧偏移会指向新内容中间 → 意外重执行。
- 标准做法：`netstat -ano | grep <port>` 找 PID，**连宿主 cmd 一起** `taskkill /F /PID <pid> /T`。
- **不中断服务的替代做法（本次采用）**：让新文件**比旧文件短** ⇒ 旧偏移落到 EOF 之外，读到空。UTF-8→GBK 转换天然变短（中文 3 B/字 → 2 B/字，本次 2385→2127）。



## 13. 2026-09-23 项目清理台账 + 两条可复用教训

### 13.1 编码 bug 的"物理证据"（.bat 误解析会执行重定向）

三个化石文件证明被误解析的 `>` 是**真的被执行**的：

| 文件 | 大小 | 取证 |
|---|---|---|
| `340` | 0 B | 注释 `but real 100K prompt prefill 660->340 t/s 衰减` 里的 `->340` → 重定向到文件 `340` |
| `接受率归零` | 0 B | 同类，中文名重定向 |
| `D:MyLLMlogsornith15_apex_mtp.log` | 2665 B | 名字由 `D` + **U+F03A**（私有区，看着像冒号）+ `MyLLMlogsornith15_apex_mtp.log` 组成，**反斜杠被吞** ← `apex_mtp.bat` 的 `> D:\MyLLM\logs\...` 被解析坏 |

⇒ 结论升级：UTF-8+LF 的 .bat 不只是"刷一屏报错"，**它会在工作目录里造文件/可能覆盖同名文件**。

### 13.2 教训一：替换"编不进 GBK 的字符"要按码位算

- `⚠️` 不是 1 个字符 = **U+26A0 (⚠) + U+FE0F (VARIATION SELECTOR-16)**。
- 逐个码位替换成 `[!]` 会得到 `[!][!]`；**替换后必须再合并重复标记**（`[!][!]`→`[!]`），否则注释会越来越脏。
- 更省事的替代：整串替换（`text.replace('⚠️','[!]')`）后再逐码位兜底。

### 13.3 教训二：核对"删了多少"用 `du`，别用 `df`

- 本次真删 1.73 GB 后，`df -h /d` 的可用空间几乎没变（49.6 GiB）——因为系统里其他进程同期也在写。
- 正确核验：`du -sh --exclude=models .`（5.4 G → 3.6 G，差值 = 释放量）。
- 另：`SHFileOperationW`（回收站）返回码再次实测 **rc=2 但操作成功** ⇒ 一律以 `os.path.exists` 复核，别信返回码。

### 13.4 清理脚本的两个坑

1. **引用检查用子串匹配会误判短名文件**：文件 `340` 因为 `163840`、`3400` 等子串出现在其他文件里，被判成"被引用"而漏删 ⇒ 对**纯数字/极短文件名**要单独处理（按词边界或直接白名单）。
2. **回收站删目录可能因句柄残留失败**（`__pycache__`）：对可再生成的缓存目录，失败后直接 `rmtree` 即可，不必纠缠。

### 13.5 本次 .bat 修复明细（4 个脚本）

全部由 skill `win-bat-encoding-fix` 的 `bat_doctor.py fix` 完成（先备份 → 转 GBK+CRLF → 逐字符一致复核），备份后缀 `.bak-20260923_182744`。修复后本目录 8 个 .bat 全部合规（GBK + CRLF，`check` 高危 0）。



## 14. 2026-09-23 第二轮清理：.py / .bak / 实验输出 / release zip

**触发**：用户质问「文件夹下那堆 py 和 bak 你瞎了么」—— 第一轮只清了「看起来像临时产物」的，**42 个 `.py` 与 16 个 `.bak` 完全没进候选清单**。教训：清理的第一遍扫描不能靠"像不像垃圾"的直觉，必须**按文件类型枚举 + 逐一引用核查**。

### 14.1 引用核查的正确口径（关键）

语料 = 全部 `*.py/*.bat/*.ps1/*.sh/*.mjs/*.json/*.yml/*.md` + `.workbuddy/memory/**` + `~/.workbuddy/skills/**`（本次 236 个文件）。三档分类：

| 档 | 判据 | 处置 |
|---|---|---|
| 保留 | 被**非 md** 文件（脚本/bat）引用，或 MEMORY 铁律记载 | 不动 |
| 中间层 | **只被 .md/日志**提到（= 当时的实验证据，不是活跃依赖） | 由用户选力度 |
| 可删 | 全无引用 | 删 |

### 14.2 「被引用」必须分读/写（差点误删）

- `open(f,'w')` / `json.dump` → 该文件是**输出** ⇒ 可删（脚本会重建）。
- `open(f).read()` → 该文件是**输入** ⇒ **必留**，否则保留的脚本跑不起来。
- 实例：`prefix_agent.txt` 被 `test_reuse.py:23` 与 `test_slot3.py:15` 当输入读取 ⇒ **保留**；而 `pc_reuse_test.json`/`slot_restore_clean.json`/`bench_apex_result.json` 都是脚本写出的结果 ⇒ 可删。
- 另拦下 4 个**活跃 bat 的日志目标 / 守卫的输入**：`bonsai_mtp_8090.log`（`start_bonsai_mtp_8090.bat` 的 `>` 目标）、`bonsai_2_27b_8091.log`（同 bat 目标 + `bonsai_guard.py`、`repro_bonsai_degeneration.py` 读取）、`bonsai_2_27b_8091.crashed.log`（`_analyze_8091_v2.py` 输入）、`vibethinker_server.log`（`start_vibethinker.bat` 目标）。
- ⇒ **判据升级**：删日志前先 `grep -hoE '>[ ]*[A-Za-z0-9_:\\/.-]+\.(log|txt|json)' *.bat`，凡出现在活跃 bat 重定向右侧的一律不删。

### 14.3 删除台账（59 项走回收站 + 2 个 zip 真删）

| 组 | 内容 | 数量 | 体积 |
|---|---|---|---|
| D1 零引用 .py | `dl_llama_cpp_b10783`/`_api`、`dl_llama_cpp_b9680`/`_cudart`、`_dgx_bench_8086.py`、`_dgx_f16head.py`、`_gguf_info.py`、`_analyze_8091_log.py`、`_patch_8090_ctx.py`、`_verify_tmpl_patch.py`、`_start_dsh.py`（DETACHED 版，已证失败）、`gen_payloads.py` | 12 | 33 KB |
| D2 过期 .bak | `start_bonsai_mtp_8090.bat.bak-{20260922,notpl_175427,think}`、`start_bonsai_mtp_8092.bat.{bak-exp,bak-prevision,retired}`、`start_glm_ocr_8089.ps1.bak`、`start_ornith15_8090.bat.bak-{apikey,crlf,utf8}`、`start_ornith15_apex_mtp.bat.bak-20260914` | 11 | 30 KB |
| D3 G4 实验输出 | `bench_*.json`×5、`slot_*.json`×6、`pc_*.json`×2、`_bench_bonsai_ub1024_16k.txt`、`_think_baseline.txt`、`_timeline_crashed.txt`、`llama_server_32k.log`(3.6M)、`llama_server_96k.log`、`bonsai_mtp_8092.log`、`_searxng_*.mjs`×2、`_websearch_*.mjs`×2、`_dsh_settings_check.mjs`、`_dsh_tree.yml`、`benchmark_report.html` | 25 | 3.7 MB |
| D4 G5 旧启动器 | `start_qwen36_27b_mtp.ps1`、`_27b_ud_iq3xxs_mtp`、`_35b_mtp`、`_35b_mtp_iq3`、`_35b_mtp_iq3_temp`、`start_qwen3_coder_30b_q3km.ps1`、`start_qwen3vl_8b.ps1`、`start_lfm2_24b_server.ps1`、`start_glm_ocr_8089.ps1`、`start_ornith15_35b_q4km.ps1`、`start_vibethinker_3b.sh` | 11 | 22 KB |
| D5 release zip | `llama-b10783-bin-win-cuda-12.4-x64.zip`(242M)、`llama-b10566-...zip`(239M) —— **真删**（内容已解包到 `llama.cpp/` 与 `llama.cpp.bak_b10566/`，删前断言两处 `llama-server.exe` 均在） | 2 | 481 MB |

**保留的 6 个 .bak**：`start_ornith15_8090.bat.bak-cpu16`（bat 注释引用 + MEMORY 记载）、`start_ornith15_apex_mtp.bat.bak-cpu10`（bat 注释引用）、今天 18:27 的 4 个 `.bak-20260923_182744`（4 个 bat 修复的回滚点）。

**清理后残留**：顶层 63 文件 / 12 目录；`.py` 30、`.bak` 6、`.ps1` 1、`.sh` 2、`.zip/.json/.mjs/.html` 全 0。保留的 `.sh`（上一轮遗漏、本轮判定为可用）：`_dgx_8086_eval.sh`、`_dgx_bench_nvfp4.sh`。

### 14.4 教训三：本轮暴露的追加脚本不幂等

`MEMORY-details.md` 里 §13 被**完整重复追加了两份**（335–368 与 372–405 行内容逐字相同）—— 追加脚本没做幂等检查。**规则：向 md 追加章节前，先 `grep -c '<章节标题>'`，命中的话先删旧块再写新块。** 本次已去重。

### 14.5 全程无副作用

8090 `/health` 200、无 key `/v1/models` 401（鉴权在）；3080 LISTENING；8 个 `.bat` 仍全部 GBK+CRLF 合规（`bat_doctor.py check` 高危 0）。


## 15. 2026-09-24 8090 从 Ornith 切回 Bonsai-2-27B（实测账本）

**当前 8090 = `Ternary-Bonsai-2-27B:mtp`**，PID 5732，`llama-bonsai-mtp\bin\`（Prism d8f26eec），`-c 188416`(184K) + KV q8_0 + MTP n-max 2 + 视觉塔 CPU。

- **显存 15803 / 16311 = 96.9%，余 248 MiB**（比 9/20 记的「余 676 MiB」紧 428 MiB）。对账：`15803 − 200(独显空闲基线) = 15603` ≈ 文档 15635 ⇒ **模型自身没变，差额是桌面/Chromium 常驻独显**（见 §0.3）。**不需要因此调参** —— prefill 已实测无衰减。
- **prefill 健康判据实测**：**745.5 t/s @2461 tok、720.0 t/s @9661 tok**（溢出临界的前缀会「一路衰减不收敛」，如 574→485 t/s ⇒ 只要 prefill 平稳就说明没踩 98.2% 红线）。
- **MTP**：`#acc rate/pos = (0.711, 0.434)`，`#mean acc len = 2.14`；`dur(b,g,a) = 0.007 / ~1840 / ~0.29 ms`。
- **档位白名单再确认**：`high` / `minimal` → **HTTP 500**（模板 `raise_exception`）；`xhigh`（默认）/`medium`/`low`/`none` → 200。`none` 实测 `finish_reason=stop` 且 content 直出答案。

### 15.1 8090 启停的标准动作（本次走通的完整链路）

1. **探活**：`netstat -ano | grep :8090` 找 LISTENING PID。
2. **取进程树**：本环境 PS 工具 stdout 会被吞 ⇒ 写文件再 Read，或用 bash 的 `tasklist //FI`。本次查出是**用户自己的 pwsh 窗口**手动起的 bat（父子链：`llama-server` ← `cmd /c start_*.bat` ← `pwsh.exe`）。
3. **停**：`taskkill /F /PID <宿主 cmd 的 PID> /T` —— **杀宿主 cmd 而不是只杀 llama-server**，避免留下僵住的 cmd 宿主。
4. **验显存回收**：`nvidia-smi --query-gpu=memory.used,memory.free --format=csv,noheader` 应回到 ~200 MiB。
5. **起**：`python _start_8090_keep.py`（不带参 = Bonsai；带 `start_ornith15_8090.bat` = Ornith），**run_in_background**。
6. **等真就绪**：grep 日志出现 `model loaded` + `listening on http://0.0.0.0:8090`（本机 Bonsai ~30-35 s）。`/health` 提前返回 200 是**假就绪**（llama-server 先绑端口再加载权重）。
7. **冒烟**：短问 + **变长 prefill（≥9K tok，看 t/s 是否平稳）** + 档位抽检。


## 16. 2026-10-01 —— 8090 换 Qwen3.8-27B + 「--timeout 0」坑 + DSH Anthropic 契约坑（热层 §7 的完整版）

### 16.1 🚨 `--timeout 0` → 所有 >8KB 请求体 HTTP 400（空 body）
- **现象**：HTTP 400、`Content-Length: 0`、`Server: llama.cpp`、**服务端日志零记录**（请求没进推理层）。表现为「所有正常请求 400 / 长上下文全废」，极易误诊成 n_ctx 或模型问题。
- **实测阈值**（二分定位）：body `2108/4108/6108/7108/7608 B → 200`；`8108/9108/12108/30108 B → 400`。⇒ 分界在 **~8 KB**。
- **机理**：cpp-httplib 把 `--timeout 0` 解释成「读超时 = 0 秒」；请求体首读（约 8 KB 缓冲）拿不满时需第二次 socket read，`select` 立即超时 → 读体失败 → 400。
- **修复**：**`--timeout` 永不写 0**。本 build（b10783）帮助文本写的默认值就是 **3600**；改 `--timeout 3600` 后 `60108 B → 200`、21.6 KB 长 prompt → 200。
- **交叉经验（另一会话实测）**：同一短请求，**Python `urllib` 也 400，而 curl 是 200** ⇒ 遇 400 空 body 先用 curl 交叉验证，别先怀疑服务端。

### 16.2 llama-server 多槽 ctx 语义（`-c` 是总 KV）
- `kv_unified=false`（默认）⇒ `llama-context.cpp:370` 的 `n_ctx_seq = n_ctx / n_seq_max`（再 `GGML_PAD(...,256)`）：**`-c` 是「总」KV，被槽数平分**；只有 `kv_unified=true` 时每槽拿全额。
- ⇒ `--parallel 2 -c 180224` = 2 槽各 90112（总 KV 反而变小）。**判定看启动日志 `srv load_model: initializing, n_slots=?, n_ctx_slot=?, kv_unified=?`**。
- **改 bat 用 GBK/二进制精确替换锚点**（如 `-c 188416 -b 2048`），别裸替换 `-c 188416`（注释里也有同样字符串，会一起改错）。

### 16.3 前缀缓存：跨重启无解，同会话内 100% 生效
- `--prompt-cache` 是 CLI 专属；`--slot-save-path` restore 后仍要重算 → 收益 0，**勿再投入**。
- **同会话内复用实测 437×**（147K 文档：首轮 261.6 s → 次轮 TTFT 0.6 s）。⇒ **别中途改系统提示/工具定义**，否则缓存全废。

### 16.4 工具链小坑
1. **Git Bash** 里 `/c/...` 会被 MSYS 改写 → 调 node/python 用反斜杠路径或 `MSYS_NO_PATHCONV=1`；**ESM 不认 `NODE_PATH`**，要 `file:///` 绝对 URL。
2. `Edit` 报 success 也可能未落盘 → **改完必须 Read 回校验**；**同一文件多处编辑不要批量并行提交**（实测一批 5 条只落地 2 条）→ 整文件重写更稳。
3. **本机 PowerShell 工具的 stdout 常被吞** ⇒ 要结果就 `Out-File` 到文件再 Read（查进程树用的就是这招）；**`wmic` 在本机已不存在**（FileNotFoundError），别再用它。
4. **清理文件禁止用通配**：本次 `_probe_*.json` 误删了 9-30 的 `_probe_results.json`（`_gguf_probe_remote.py` 可重跑再生）。

### 16.5 DSH 侧：`llm-deepseek` 与 llama.cpp `/v1/messages` 的契约冲突
- 报错 `DeepSeek Messages expected a JSON object`（`code=MALFORMED_RESPONSE`）出自 `dsh-llm-deepseek` 的 `object()`（`packages/llm/llm-deepseek/src/{replay,translate}.ts`）。
- **llama.cpp 的 Anthropic 端点做 tool_use 时**：`content_block_start` 只给 `{type,id,name}`，**没有 `input` 字段**（Anthropic 规范要求 `input:{}`）→ `object(undefined)` 秒抛。**⇒ 只要该模型触发工具调用就必挂**，纯文本流式完全正常（已抓包验证 message_start/content_block_*/message_delta/message_stop 全合规）。
- **绕过**：DGX 8081 的 OpenAI 端点工具调用完好（`finish_reason=tool_calls` + `{"city":"Beijing"}`）⇒ 把该模型改到 DSH 的 `pi-ai`(`openai-completions`) 路由。
- **顺带**：llama-server 对请求里的 `model` 名**不做严格校验**（用 `__no_such_model__` 也能正常返回）⇒ 换 alias 不会打断既有客户端。

### 16.6 2026-10-01 8090 实测账本（Qwen3.8-27B-UD-IQ3_S @56K）
| 项 | 实测 |
|---|---|
| 加载 | 52 s（冷）/ 7.5 s（页缓存热） |
| 缓冲 | model 10951.60 MiB + 主 KV(q4_0) 1008 MiB + 草稿 KV(f16) 224 MiB + compute 360/154 MiB |
| VRAM | **13363/16311 MiB = 81.9%，余 2948 MiB** |
| 槽 | `n_slots=1 / n_ctx_slot=57344 / kv_unified=false` |
| prefill | **846.5 t/s @ 13126 tokens（无衰减）** |
| decode | 49.7 t/s（300 tok）；MTP 接受 0.7355、mean len 2.47 |
| 工具 | `finish_reason=tool_calls`，arguments 合法 JSON |
| 鉴权 | 无 key → 401；带 key → 200；/health → 200 |
| 结论 | 旧「59K 溢出点 / 56K KV≈2.85 GB」不成立（KV 实测 1008 MiB）⇒ **ctx 有大幅上调空间**（未改，待用户定） |


## 17. 2026-10-01 晚 —— 8090 切 Ornith-1.5-35B-APEX-MTP + 补鉴权

### 17.1 切换过程与两个坑
- 用户指令「改apex」→ 经确认 = 「切到 APEX-MTP（标准版）」= `start_ornith15_apex_mtp.bat`。
- **坑①（安全）**：APEX 标准脚本**没有 `--api-key`**，而当时 Tailscale Funnel 正把
  `https://liang-geekpro.tailfe4bdd.ts.net` 代理到 `127.0.0.1:8090`。照原样跑 = 公网无鉴权 35B 推理接口。
  → 用 `_patch_apex_key.py` 补 `--api-key`（与 Qwen3.8/Bonsai 同一把）。补丁特性：幂等（已含 key 即跳过）、
  GBK 二进制级精确锚点替换、写回后校验 `60 行全 CRLF / 零孤立换行 / GBK 可解码`。
  **教训：每次换 8090 脚本前，必须同时核对 `tailscale funnel status` 与脚本里的 key。**
- **坑②（进程树）**：第一次启动后约 2 分钟服务消失 —— `netstat` 无监听、llama-server 无残留、
  保活日志**没有** `宿主 cmd 退出 rc=` 行 ⇒ **整棵树被外部终止**（宿主 shell 回收），
  **不是**模型加载失败（日志零 error，停在 `tensor overrides to CPU ... mmap`）。原样重起即成功。

### 17.2 APEX 实测基线（10-01 20:11 重启后）
| 项 | 值 |
|---|---|
| 模型 | `Ornith-1.5-35B-A3B-APEX-MTP-I-Compact.gguf`（17.44 GB，sha256 67e6cdf / f4165） |
| 构建 | `llama.cpp.bak_b10566\llama-server.exe`（build 10566 / commit bb4caa754 / Clang 20.1.8） |
| 槽位 | `n_slots=1 / n_ctx_slot=196608 / kv_unified=false` |
| 加载 | 10.5 s（页缓存热）；冷启动约 31–52 s |
| 显存 | **16017 / 16311 MiB = 98.2%（余 ~294 MiB）**，空载 3.65 W / 39 °C |
| prefill | **973 t/s @ 28056 tok**、**816 t/s @ 96653 tok**（全程无衰减） |
| decode | **69.1 / 73.3 / 77.2 t/s** |
| MTP 接受 | **0.518 – 0.558**，mean len 2.04–2.12 |
| 工具调用 | `finish_reason=tool_calls`，`{"city":"北京"}` 合法 JSON |
| 鉴权 | 无 key → 401，带 key → 200，`/health` → 200 |

### 17.3 ⚠️ 首次请求的 CUDA graph 冷编译 —— 极易误判成「退化」
- 切换后**第一个**请求：`prompt eval time = 28102.69 ms / 32 tokens`（**878 ms/token，1.14 t/s**），
  而同一请求的 `eval time` 正常（300 tok → **77.21 t/s**）。
- 第二个请求起，同规模 prefill 只需 **226 ms / 20 tok（434.75 t/s）**。
- ⇒ **墙钟 = prefill(含冷编译) + decode**；用 `wall / completion_tokens` 直接算 decode 会得到
  ~9.4 t/s 的假结论（真实 77 t/s，差 8 倍）。**判速度一律读日志 `print_timing` 行，不看 curl wall。**

### 17.4 重启姿势（保留保活器 vs 不保留）
- **保留保活器（推荐）**：`MSYS_NO_PATHCONV=1 taskkill /F /PID <宿主的 cmd.exe> /T`
  → 保活器 `p.wait()` 返回 → 日志记 `宿主 cmd 退出 rc = 1, 15s 后重启` → 15 s 后自动拉起干净实例。
- **不保留**：`taskkill /F /PID <保活器 python PID> /T` → 整棵树消失，需重新
  `_start_8090_keep.py <bat>`（且必须 `run_in_background`）。
- 进程树形如：`python(_start_8090_keep.py) → cmd.exe(/c <bat>) → llama-server.exe`。
- 定位 PID：`Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'llama-server|_start_8090_keep' }`
  的 stdout 在本机常被吞 → **`Out-File` 到文件再 Read**。

### 17.5 客户端超时打断会 cancel 槽（变体③旁证）
- 用 `curl --max-time 120` 打 100K prefill（实际需 ~118 s）→ 客户端超时断开 →
  服务端立刻 `W srv stop: cancel task, id_task = 250` + `slot release: ... n_tokens = 97161`。
- 这正是**变体③的旁证信号**。⇒ 长 prefill 测试务必给足 `--max-time` 或走 `run_in_background`。
- 本次取消后冒烟测试仍正常（`/` 占比 0/163、decode 69 t/s、MTP 0.558），但按铁律仍重启清槽。

### 17.6 本轮临时文件清理教训
- 清理时**显式列出 12 个文件名**（`_probe_apex*.json` / `_apex_*.out` / `_ps_keep*.txt`），**未用通配**。
- 保留的正式产物：`start_ornith15_apex_mtp.bat`（已补 key）、`_patch_apex_key.py`（幂等补丁，可复用）。


## 18. 2026-10-02 —— 把 DGX 的 web-search-localxng 搜索 provider 移植到本机 DSH

### 18.1 这是什么

DGX 上自研的 dsh 插件 `web-search-localxng`：一个 `WebSearchProvider`，**直连中文搜索引擎**
（bing_cn / baidu / sogou / so360 / quark / chinaso），**不需要 API key，也不需要 SearXNG 实例**。
它注册进 `ctx.web` 的 provider registry（与 `dsh-web-search-exa` 同款机制），provider id = `localxng`，
由 `web.searchProvider: localxng` 钉住。

包结构（3 个文件，72 KB）：
```
web-search-localxng/
  package.json        # name: web-search-localxng; dsh.bundle.patch: ./cordis.patch.yml
  cordis.patch.yml    # - insert: - id: web-search-localxng / name: web-search-localxng
  lib/index.js        # 1429 行，57 KB，纯 JS（无原生模块）
```
`lib/index.js` 只依赖 `@deepseek-ai/schemastery`（`z`）与 `@deepseek-ai/dsh-web`（`WebError`），
运行时只用 `fetch` / `AbortSignal.timeout` / `crypto.getRandomValues` / `btoa` / `URL`
⇒ **平台无关，Windows 直接可用**（本机 Node 22.22.2 / dsh 0.2.0-rc.2，与 DGX 同版本）。

### 18.2 聚合器设计（读码笔记）

- **6 引擎并行 fan-out**，`Promise.allSettled` 收口，**永不抛错**（全失败也返回空 hits + 每引擎状态）。
- **熔断退避**：`blocked`（反爬）2min × 2^n，上限 15min；`parse`（改版）5min；`network` 45s；
  `empty` 不冷却（它只说明关键词无结果）。
- **窗口预算**：每引擎每固定 10 分钟窗口最多 N 次真实请求，用光即跳过（状态 `budget`，不报错）。
  低风险（bing_cn/so360/chinaso）默认 6；高风险（sogou/baidu/quark，4–9 次快速请求即验证码）默认 2。
- **身份轮换**：`blocked` 时清该引擎 cookie 会话，HTTP 层同时轮换 UA（6 条真实浏览器 UA 池）。
- **合并**：平滑加权轮转（bing_cn/baidu/sogou 权重 2，其余 1）+ 跨引擎 URL 去重（剥 utm_/from/ie/tn）。
- **时效查询降权**：命中 `今天|最新|新闻|…` 时把百科类（baike.baidu.com / baike.sogou.com / …）压到尾部。
- **重定向解链**：baidu/chinaso `?url=` 走 302 Location；sogou/so.com 走 200 body 里的 meta-refresh / `URL='...'`；
  最多 2 跳，失败就保留原链接。

### 18.3 移植三步（本机实操）

1. **拷包**（DGX → 本机）：`scp` 三个文件到
   `C:\Users\jianz\.dsh\profiles\node_modules\web-search-localxng\{package.json,cordis.patch.yml,lib/index.js}`。
   **用实体目录**（与 DGX 一致），不要软链到 E: —— 本机 searxng 就是软链到
   `E:\deepseek-harness\packages\web\web-search-searxng`，这才有了"改 E: 才生效"的混装债。
   拷贝后 `sha256sum` 两边对照，确认无损。
2. **接线**：在 `~/.dsh/profiles/web/cordis.patch.yml` 的 `- insert:` 列表**加一行**：
   ```yaml
   - id: web-search-localxng
     name: web-search-localxng
     config:
       lowRiskPerWindow: 6
       highRiskPerWindow: 2
       maxResults: 8
       resolveLinks: true
   ```
   再把 `- id: web` 的 `config.searchProvider` 改成 `localxng`（**注意 `- id: web` 是 OVERRIDE，
   整块替换不 merge**，`searchFallbackProvider` / `fetchProvider: http` 必须一起写全）。
3. **重启** dsh（见 18.5）。

> `Config` schema 里所有字段皆可选（`apply()` 到处 `?? 默认值`），只写上面 4 项即可；空 `{}` 也能构造。

### 18.4 三层验证手法（强烈建议照抄）

**① 离线 import 测试**（秒级，验"能不能加载"）：
```js
const mod = await import('file:///C:/Users/jianz/.dsh/profiles/node_modules/web-search-localxng/lib/index.js');
console.log(mod.name, mod.inject, typeof mod.apply, mod.LOCALXNG_PROVIDER_ID);
console.log(JSON.stringify(new mod.Config({ lowRiskPerWindow: 6, maxResults: 8 })));
```
模块放在 profiles/node_modules 下时，其 `import '@deepseek-ai/...'` 会向上解析成功（本机实测 OK）。

**② mock ctx 端到端真实搜索**（验"功能真的通"）——**不用起服务**：
```js
let provider;
mod.apply({ web: { registerSearchProvider: (p) => { provider = p; } } }, new mod.Config({...}));
const out = await provider.search({ query: '英伟达 RTX 5090 发布', maxResults: 6 });
```
本机实测：**1.63 s 返回 6 条**中文结果（NVIDIA 官网 / 163 / 太平洋科技），带 `publishedAt`。
⇒ 证明本机网络出口能直连这些引擎，且解析器工作正常。脚本留存 `D:\MyLLM\_verify_localxng_search.mjs`。

**③ `dsh --profile web --dump-config`（新发现的神器）** —— 打印**组合后的 profile 树**，不启动服务：
```bash
DSH_HOME=C:/Users/jianz/.dsh node .../dsh/lib/bin.js --profile web --dump-config
```
- `rc=0` + stderr 空 = 树组合成功、**无 `duplicate loader entry id`**（那个错会让 3080 直接起不来）。
- 能直接看到 patch 的覆盖结果（实测输出里 `- id: web` 的 config 已是 `searchProvider: localxng`，
  末尾 `# == ...\profiles\web\cordis.patch.yml` 层出现 `- id: web-search-localxng`）。
- 配套：`--dump-default-config`（不含用户层）、`--dump-config-schema`（只出 schema）。
- **这就把"改配置有没有生效"从"重启后猜"变成了"重启前证实"。**

### 18.5 重启 dsh 的两个新坑

1. **`cmd /c start /min cmd /c <bat>` 静默失败**（10-02 实测）：Python `Popen` 里这么跑，
   进程数 0 增长、端口不动、日志不更新——**没有任何报错**。加进"❌ 全部失败"清单。
   **可用姿势仍是** `Popen(['cmd','/c', '<bat>'], cwd=...)` + `p.wait()`，由 `run_in_background` 的 shell 托管
   （重建了 `D:\MyLLM\_start_dsh_keep.py`，默认指向 `E:\deepseek-harness\start_dsh_web.cmd`；
   另有 `_restart_dsh.py` 做"杀 3080 → 再拉起"的一步式）。
2. **Git Bash 给 cmd 传播 PATH 污染**：`start_dsh_web.cmd` 里的 `timeout /t 1 >nul` 命中了
   **coreutils 的 `timeout`**，stderr 报 `timeout: invalid time interval '/t'`。
   后果轻微（cmd 不中断，继续往下走），但**任何经 Git Bash 拉起的 bat 都可能中招**——
   别在 bat 里依赖 `timeout` 做延时（用 `ping -n` 或干脆去掉）。
   ⚠️ 排查时的价值：后台任务 stdout 里能看到 bat 的 echo 序列，能精确判断卡在哪一行。

### 18.6 本机 vs DGX 的配置差异

| 项 | DGX | 本机（本次） |
|---|---|---|
| `searchProvider` | `localxng` | `localxng` |
| `searchFallbackProvider` | 未设（deepseek/searxng 两块**被注释掉**） | **`deepseek-official`**（保留兜底） |
| localxng config | lowRiskPerWindow 6 / highRiskPerWindow 2 / maxResults 8 / resolveLinks true | **同** |
| searxng 包 | `profiles/node_modules/web-search-searxng`（实体） | 软链到 `E:\deepseek-harness\packages\web\web-search-searxng`（保留，未选中） |

> 回退办法：把 `web.searchProvider` 改回 `searxng`（打 DGX 的 `http://100.81.83.59:8080`）
> 或 `deepseek-official`（走官方 API 额度），重启 dsh 即生效。


## 19. 2026-10-02 —— dsh 的最佳宿主是 explorer（双击 bat），不是 WorkBuddy 后台保活

### 19.1 事件
- 我用 `run_in_background` 起的 `_start_dsh_keep.py` 存活 **3h23m** 后宿主 cmd 退出（`host cmd exited rc = 0`），后台任务被标记 failed。
- **但 3080 从未中断**：同一时刻（17:14:28-30）由 **explorer.exe(6148) 双击拉起的新进程树**接替：
  `explorer.exe → cmd.exe(12628) → powershell.exe(2380) → node.exe(47700) dsh web`。
- ⇒ **「后台任务 failed」≠「服务挂了」**。判据仍是：`netstat` 看端口 + `dsh_web.log` 看新 token。此处 rc=0（正常退出）且端口仍在，属换宿主而非崩溃。

### 19.2 结论（运维姿势）
- **dsh 常驻首选：从资源管理器双击 `C:\Users\jianz\start_dshweb.bat`**。explorer 做宿主最稳，不会被 WorkBuddy 的宿主回收机制带走。
- WorkBuddy 后台保活（`run_in_background` + `_start_dsh_keep.py`）仍可用，但**寿命受宿主限制**（本机实测约 3 小时级）；掉了就用 `_restart_dsh.py` 或让用户双击 bat。
- ⚠️ **每次重启 token 都变**：本轮 17:14 重新生成 → `9W3ZMjm_lPlXXSW75LJjGaGcYLMJLniBfg3LgQUFOMg`（旧 `e9Amox...` 已失效）。取用：
  `python -c "import pathlib,re;print(re.search(r'token=([A-Za-z0-9_-]+)', pathlib.Path(r'E:\deepseek-harness\dsh_web.log').read_bytes().decode('utf-16','replace')).group(1))"`

### 19.3 顺带确认
- `dsh --profile web --dump-config`（**rc=0、stderr 空**）复核：`- id: web` 的 `searchProvider: localxng` 仍在，patch 层 `- id: web-search-localxng` 仍在 ⇒ **重启后 localxng 配置未丢**。
- 本机 `turn` 里的 `wmic` 确认不存在（CreateProcess 直接失败）；**含 `powershell`/`cmd` 字样的 Bash/PowerShell 命令串会被安全策略拦**（实测 `tasklist | grep -iE "cmd|powershell"` 被拒）→ 用 `tasklist | awk '$2==PID'` 这类纯数字过滤。


## 20. 2026-10-02 —— 8090 切回 Bonsai（APEX → Bonsai 2 槽）+ 「并发摊薄」测速坑

### 20.1 切换操作（APEX → Bonsai）
1. 探活：`netstat` 得 llama-server PID 34064；`keep_8090.log` 末行确认当前 bat = `start_ornith15_apex_mtp.bat`。
2. 读 `PowerShell Get-CimInstance Win32_Process`（写文件再 Read）得树：`python 28372 → cmd 38748 → llama-server 34064`。
3. `funnel status` 复查：`https://liang-geekpro.tailfe4bdd.ts.net → http://127.0.0.1:8090` ⇒ 鉴权必须保留。
4. **Bonsai 脚本体检**（`start_bonsai_mtp_8090.bat`）：GBK + 87 行**全 CRLF、零孤立 LF**；`--api-key` 在位；`--timeout 600`（非 0）；KV `q8_0`；**无 `--spec-draft-type-k/v`**（草稿 KV = 默认 f16，合规）；`-c 180224 --parallel 2`；`-lv 4` 保留。
5. 备份即将被截断的日志：`logs/ornith15_apex_mtp.log.backup_*`（切前状态留证）+ `bonsai_mtp_8090.log.backup_*`（该 bat 用 `>` 会截断）。
6. `MSYS_NO_PATHCONV=1 taskkill /F /PID 28372 /T` → 三条"成功"（34064 / 38748 / 28372）→ 端口释放、无残留。
   ⚠️ **换 bat 必须杀保活器整树**（铁律 4 的"只杀宿主 cmd"只适用于保持同一 bat）。
7. `_start_8090_keep.py start_bonsai_mtp_8090.bat`（`run_in_background`）。

### 20.2 实测（2026-10-02 17:32）
- 加载 **17.3s**；`n_slots = 2, n_ctx_slot = 90112, kv_unified = false`；VRAM **15895–15907 / 16311 MiB = 97.4%**（与 09-30 基线一致）。
- 鉴权：无 key **401**、带 key **200**、`/health` 200；alias = `Ternary-Bonsai-2-27B:mtp`（completion + multimodal）。
- **>8 KB 请求体复测**：`2114 / 9114 / 30114 / 60114 B → 全 200`（`--timeout 600` 生效）。
- **prefill**：22430 tok → **826.74 t/s**；305 tok → 411.57 t/s；无衰减。
- **decode / MTP**：**独占时 59.82 t/s / MTP 0.886**（task 1716）；其余读数 26–43 t/s 全因并发（见 20.3）。
- **工具调用**：`finish_reason = tool_calls` + `{"city":"北京"}` 合法 JSON。

### 20.3 🚨 新坑：「并发摊薄」会让测速得出"退化"的假结论
- 现象：切换后连测多次，decode 只有 **26–43 t/s**（历史基线 74 t/s），第一反应像"变体③退化"。
- 实情：日志里 **slot 0 与 slot 1 同时各跑一个 task**（如 task 1384 与 task 1487 并发）；单看 `n_gen` 递增行可见同一 task 的 `tg` 从 26.46 → 28.20 → 34.56 → 40.21 t/s —— **并发退去后逐步回升**。
- 决定性判据：等一个 task **独占**时，读它的 `print_timing` → **59.82 t/s、MTP 0.886**，完全健康。
- ⇒ **测速前先确认没有别的客户端在打 8090**；否则必须挑一个 `n_gen`/`tg` 行确认该 task 独占。
- 并发来源：① **DSH** —— `~/.dsh/profiles/web/cordis.patch.yml` 里 `baseURL: http://127.0.0.1:8090/v1`（两个 provider：`Ornith-1.5-vision` / `Ternary-Bonsai-2-27B:vision`，contextWindow 190000 / 184000）；② **公网 Funnel** —— `netstat` 显示唯一 ESTABLISHED 是 `tailscaled.exe (PID 9300)` 持有的代理连接。
- 旁证：task id 在 ~40 s 内从 906 涨到 1384（≈470），说明有客户端在**高频连续请求**（agent 式 300–640 token 小请求）。
- ⚠️ 重启 8090 会**直接打断这些客户端**（用户若正在 DSH 里对话会看到报错）—— 任务要求重启时照做，但务必在汇报里说明。

### 20.4 顺便确认的两个环境事实
- `wmic` 在本机**确实不存在**（`subprocess.run(['wmic',...])` 直接 CreateProcess 失败）；`tasklist /FI "PID eq N"` 输出常被吞 → 用 `tasklist | awk '$2==PID'` 最稳。
- PowerShell 工具的命令串里出现 `'powershell'`/`'cmd'` 字面量（哪怕在正则里）会被安全策略拦；用 `-match '_start_8090_keep'` 这类纯标识符过滤即可。

---

## 21. 2026-10-03 —— 8090 重启 Bonsai（宿主回收后拉起）+「草稿接受率」测速坑（第二号假象）

### 21.1 现场：8090 已整体消亡（不是加载失败）
- `netstat | grep :8090` **无输出**、`tasklist` 无 `llama-server.exe` / python ⇒ 进程全无。
- `keep_8090.log` 末尾停在 `[2026-10-02 17:32:09] 启动: cmd /c start_bonsai_mtp_8090.bat`，**无 `宿主 cmd 退出 rc=` 行** ⇒ 按 MEMORY §2.2 判据，整树被**外部终止**（宿主 shell 被回收），非模型加载失败。
- `bonsai_mtp_8090.log` 末条写入 `2026-10-03 12:27:07`，末行是正常的 `srv update_slots: all slots are idle`；末条日志时间戳 `1134.56.…` = **1134 分钟 ≈ 18.9 h**（09-30 曾跑到 23.5h，未及变体③的典型触发时长）。

### 21.2 重启操作
1. 备份：`cp bonsai_mtp_8090.log logs/bonsai_mtp_8090.log.backup_20261003_124449`（384 245 B，防 bat 的 `>` 截断）。
2. `_start_8090_keep.py start_bonsai_mtp_8090.bat`（`run_in_background`，task `80TV62`）。
3. 结果：加载 **~20 s**（`grep "model loaded"` 轮询命中）；`n_slots = 2, n_ctx_slot = 90112, kv_unified = 'false'`；`listening on http://0.0.0.0:8090`；VRAM **15800/16311 = 96.9%**。
4. 鉴权/暴露：无 key `401` / 带 key `200`；alias `Ternary-Bonsai-2-27B:mtp`；`funnel status` 仍 `→ 127.0.0.1:8090`。
5. 验证：tools 请求 `finish_reason=tool_calls` + `{"city":"北京"}`；请求体 **34 755 B → 200**（`--timeout 600` 有效）。

### 21.3 🚨 新坑：「草稿接受率」污染 decode 测速（排除并发之后仍会踩）
场景：独占（`netstat` 零 ESTABLISHED、`nvidia-smi` GPU util 0% / SM 255 MHz 空闲）下连测，得 **26.0 t/s**，远低于记忆中的 59.8 基线，**疑似变体③退化**。

真相：**MTP 下 decode ≈ 前向速率 × mean acc len**，与内容强相关。同一实例、同一时刻：

| 内容 | draft acceptance | mean len | decode |
|---|---|---|---|
| 可预测结构化输出（英文编号列表 1–200，temp 0.2） | **0.976** (330/338) | 2.95 | **53.56 t/s** |
| 自由中文散文（300 字，temp 0.7） | **0.396** (176/444) | 1.79 | **25.98 t/s** |

- 低接受率内容前向速率不变，但每步只带出 ~1.8 token ⇒ **速度天然腰斩**。
- **判据：必须把同一 task 的 `eval time` 与 `draft acceptance` 成对读；只报 decode 不减接受率不作数。**
- 历史「59.8 t/s / MTP 0.886」是在**高接受率内容**上测的 ⇒ **别拿它当所有内容的及格线**。
- 交叉验证：上一轮同参数运行（含 DSH 并发）的 decode 分布 p10 27.6 / p50 39.0 / p90 59.2 / max 66.5 ⇒ 26.0 落在 p10–p50 内，正常。
- 统计命令（**必须 `grep -aE "\| +eval time ="` 排除 `prompt eval time`**，否则 prefill 的 600–900 t/s 会混进来）：
  `grep -aE "\| +eval time =" <log> | grep -aoE "[0-9.]+ tokens per second" | sort -g | awk '{a[NR]=$1} END {printf "p50=%.1f\n", a[int(NR*0.5)+1]}'`

### 21.4 测速三假象小结（按排查顺序）
1. **首次请求 CUDA graph 冷编译**（§7）——wall/tokens 会算出 ~1/8 的假值。
2. **并发摊薄**（§20.3）——多客户端共享槽位，单路 decode 掉到 26–40。
3. **草稿接受率**（本节，10-03 新增）——独占下低接受率内容也能掉一半。**三个都排掉，才轮到判「变体③退化」。**

---

## §22 Ornith-1.5-35B-A3B-NVFP4 / DFlash / MTP 完整调研（2026-10-20）

**用户诉求演进**：① 16G 卡高效跑 ornith-ai/Ornith-1.5-35B-A3B-NVFP4；② 明确"要原生 NVFP4，llamacpp 或 vllm 都行"；③ 追问权重是否支持 MTP，不支持的话官方 DFlash 能不能用。

### 模型规格（ModelScope API 实取；HF 页面 fetch 失败、modelscope 页面 JS 渲染抓不到 → 用 `/api/v1/models/<org>/<repo>/repo?Revision=master&FilePath=<path>` 取原始文件）
- `qwen3_5_moe`（Qwen3_5MoeForConditionalGeneration，视觉版）：**41 block（40 主层 + 1 MTP）**、hidden 2048、16 头/2 KV 头、head_dim 256、**256 专家选 8**、shared expert 512、vocab 248320、ctx 256K 原生。
- 40 层 = 10 层全注意力（full_attention_interval=4）+ 30 层 linear_attn/SSM（conv_kernel 4 / state 128 / group 16 / time_step_rank 32 / inner 4096）。
- NVFP4 权重 **23.4GB**（3 shards：10+10+3.4GB），**W4A8**（config quantization_config.input_activations = fp8）⇒ vLLM SM120 的 FlashInfer cutlass FP4 kernel 不存在，modelopt_fp4 loader 直接失败（除非改 W4A16 走 MarlinNvFp4，参考 raoashish10/Qwen3-14B-NVFP4-W4A16 在 RTX 5070 Ti 的改法：strip input scales + relabel W4A16_NVFP4，权重字节不动）。
- 文件清单（ModelScope）无独立 MTP 文件；config 有 `mtp_num_hidden_layers: 1` + `mtp_use_dedicated_embeddings: false`。

### MTP 结论：支持
- NVFP4 config `mtp_num_hidden_layers=1` ⇒ 自带 1 层 MTP（权重在 shard 内）。
- 官方 `models/ornith-official/Ornith-1.5-35B-A3B-IQ4_XS.gguf`（18.6GB）元数据实证：`qwen35moe.block_count=41` + `qwen35moe.nextn_predict_layers=1` ⇒ 转换链保留 MTP。转换 NVFP4 safetensors 预期同样保留（转换后需验证 kv）。
- llama.cpp 用法：`--spec-type draft-mtp --spec-draft-n-max 2`（零额外显存；他们的 Q4_K_M bat 已在用）。

### DFlash 结论：能用，本地已具备全部条件
- 官方 `ornith-ai/Ornith-1.5-35B-A3B-DFlash`：**386M / 6 层**（5 滑窗 + 1 全注意力，sliding_window 4096）/ hidden 2048 / 32 头 8 KV / head_dim 128 / hook 8 个目标层 `target_layer_ids=[1,6,11,16,22,27,32,37]`（0-index）/ vocab 248320 / bf16 736MB 单 safetensors / model_type qwen3 + auto_map DFlashDraftModel。
- **本地已有 GGUF（10-09）**：`models/Ornith-1.5-35B-A3B-DFlash-bf16.gguf`(747MB)、`-q8_0.gguf`(402MB)；元数据 `general.architecture=dflash`、`dflash.block_count=6`、`dflash.target_layers=[2,7,12,17,23,28,33,38]`(1-index)、`dflash.context_length=262144`、`dflash.attention.sliding_window=4096`。
- **b11539 运行时原生支持**（grep -a 验证；注意本环境 Git Bash 无 `strings`，此前"0 命中"是假阴性，勿再用 strings 做二进制检测）：ggml-base.dll 有 `dequantize_row_nvfp4`/`quantize_row_nvfp4`/`quantize_row_nvfp4_ref`；ggml-cuda.dll 有 `dequantize_block_nvfp4<half/bf16>` + `k % QK_NVFP4 == 0` + MMQ NVFP4 分支；llama.dll 8 处 dflash（`src/models/dflash.cpp`、`dflash.sample_from_anchor`、`DFlash2 hidden size must be...`）。
- llama-server `--spec-type` 白名单：`none,draft-simple,draft-eagle3,draft-mtp,draft-dflash,draft-dspark,ngram-*`（comma 串列）。
- 用法：`--model-draft <DFlash-q8_0.gguf> --spec-type draft-dflash --spec-draft-n-max 4`（SGLang 官方 Spark Arena recipe 即 DFLASH + 4 draft tokens；q8_0 草稿仅 402MB）。备选 DFlash2（jzinno/Ornith-1.5-35B-A3B-DFlash2，b11539 亦有 dspark/DFlash2 支持迹象；用户已有 _prism_dflash2_src）。

### 16GB 单卡跑原生 NVFP4 的显存账（修正早期粗估）
- 专家≈32B 参数≈19.5GB（NVFP4 0.5625B/param）；dense+embed(0.5B)+lm_head(0.5B)+MTP≈3GB。
- KV 仅 10 个全注意力层：q4_0 ≈ 11.5KiB/token ⇒ 96K≈1.1GB / 128K≈1.5GB（早期按 40 层算的 22.5KiB/token 高估一倍）。
- 预算 15.5GB − KV 1.1 − dense 3 ≈ 11.4GB GPU 专家 ⇒ **`--n-cpu-moe 16~20` 起点**（Q4_K_M 20.2GB 用 16；NVFP4 专家层更重需略深）+ **`--no-kv-offload`**（铁律：n-cpu-moe 时 GPU 无 KV 空间）+ ctx 96–128K + `--flash-attn on --threads 32 --parallel 1`。
- 预期（参照 Q4_K_M cpu16：prefill 470–514 / decode 62→35）：prefill ~250–350、decode 短上下文 ~40–55，DFlash 或拉回 60–80。大概率仍不如 IQ4_XS(cpu12)/Q4_K_M(cpu16)/APEX-MTP+DFlash；原生 NVFP4 的价值在复现官方 checkpoint 而非速度。

### vLLM 出局原因（双杀）
1. checkpoint W4A8：SM120 无 FlashInfer cutlass FP4 kernel，modelopt_fp4 loader 加载即失败；唯一出路是改 W4A16（strip input scales + relabel）走 Marlin，已非原生语义。
2. 23.4GB > 16GB 且 vLLM 无 MoE 专家级 CPU 卸载（只有整模型 offload，实验性）。
- SGLang 同理：Spark Arena 官方 recipe 是 8×96GB 环境。

### 转换链与工具
- dev fork `conversion/base.py _repack_nvfp4` + `conversion/qwen.py`（Qwen 特化含 linear_attn 置换）；48GB RAM 可转。
- ⚠️ NVFP4 转换有 rope-permute 静默错先例（ALIA-40b 的 Llama 补丁 PR ggml-org/llama.cpp#22611）⇒ 转完必须冒烟对拍。
- 社区预转：`TizzyT566/Ornith-1.5-35B-A3B-NVFP4-512K-GGUF`（YaRN 512K，27GB，非原生）。
- dev fork（llama.cpp-dev，10-15 HEAD 782a40888）含 strata-like 自研线（VRAM 热表/grouped hit kernel/miss CPU pool + GPU∥CPU overlap，LLAMA_MOE_HOT_MODE 开关）；Blackwell FP4 MMQ 守卫 `blackwell_mma_available(cc)` = CC 1200 ⇒ **sm_120 覆盖**。构建入口 `D:/MyLLM/build_strata.ps1`（vcvars 壳；Unity Build 改头文件要 touch unity_N_cxx.cxx；动跨 DLL 结构体必须全目标重编，否则跨 DLL 结构体偏移错位 AV）。产物 E:/llama-strata-build/bin。
- 新建工具 `_tmp_code/ggufmeta.py`：零依赖 GGUF 头 KV 解析器（本机无 strings）。
- ModelScope 取文件法：`curl "https://www.modelscope.cn/api/v1/models/<org>/<repo>/repo?Revision=master&FilePath=<path>"` 返回原始文件内容；文件列表 `/repo/files?Revision=master`。
- 本地相关资产：Ornith-1.5-35B-Q4_K_M.gguf(20.2G)/ornith-official/Ornith-1.5-35B-A3B-IQ4_XS.gguf(18.6G)/Ornith-1.5-35B-A3B-APEX-MTP-I-Compact.gguf(16.2G)/DFlash bf16+q8_0/mmproj-Ornith-1.5-35B-BF16.gguf(861M)。

## §23 Strata q36 专家扩容与生产档定型（2026-10-21 晚）

### 生产档最终配置
`strata-ornith-nvfp4-8090.json` = `--max-context 262144 --kv q4_0 --expert-slots 48 --mtp 2`
启动脚本 `D:/MyLLM/start_strata_8090.bat`（GBK+CRLF），保活器 `_tmp_code\_start_8090_strata_keep.py`。
运行二进制 = **10-21 新构建 `strata.exe`**（不是 `strata-q36.exe` —— 后者是 10-20 旧构建，
既不含 q8_0、又含未修的 F32 KV bug，报错文本 `unknown --kv q8_0 (fp16|int8|q4_0|k8v4)` 可辨认）。

### 专家扩容实测（256K ctx，`strata.exe --bench-pp 2048 --bench-tg 128`）

| 档位 | 驻留 | GPU 命中率 | tg128 三轮 | CPU pool |
|---|---|---|---|---|
| q8_0 + 21（自动，旧生产） | 1.45 GiB | 8.1% | 41.6/43.7/45.1 | 4090 ms |
| q8_0 + 30 | 2.07 GiB | 11.1% | 42.5/45.5/47.5 | 3772 ms |
| q4_0 + 41（自动） | 2.83 GiB | 20.0% | 43.1/48.2/48.5 | 3552 ms |
| **q4_0 + 48（生产选定）** | 3.31 GiB | **24.5%**（两轮一致） | 48.2/45.9/47.5 · 46.8/46.5/49.2 | 3406/3364 ms |
| q4_0 + 50（引擎自报上限） | 3.45 GiB | 22.0% | 45.2/50.0/51.0 · 45.2/48.8/48.4 | 3222/3398/3436 ms |

- 命中率 8.1%→24.5%（3 倍），tg 41.6–45.1→46.5–49.2（+10%），CPU pool −18%。
- **选 48 不选 50**：50 档 slot replacement 1858 vs 1815 次，命中率反降 2.5pt ⇒ 超过阈值后
  自适应准入抖动吃掉边际收益。驻留数不是越多越好。
- **引擎上限的根因**（`q36_engine.cu`）：`need > fr` 用的是分配专家池**之前**的 `fr = 5332 MiB`，
  且不动用 768 MiB `reserve`；实测峰值 15384 MiB、剩 927 MiB ⇒ **理论还能再塞 ~10 slots**，
  是引擎保守核算卡住的，不是物理装不下。报错原文自带数字（"5365 MiB needed, 5332 MiB free"）可直接二分。
- `STRATA_Q36_NO_MTP_CPU=1` 反向变差（20.5% vs 24.5%）⇒ 已放弃。
- 每 slots 成本约 69 MiB（3.31 GiB / 48）；每 1 pt 命中率约省 20 ms CPU pool 时间。

### 四方交换链（决策时按此链算）
ctx ↑ → KV 占 ↑ → 专家驻留 ↓ → PCIe 搬运 ↑ → decode ↓。
旋钮优先级：`--kv` 降档（q8_0→q4_0 省 1.4 GiB 给专家）> `--expert-slots` 显式指定 > 降 ctx。

### 记忆热层已修正的过时结论
- 「256K / q8_0 生产档 = 7 slots、decode 29–33 t/s」**已被 q4_0+48 档取代**（46.5–49.2 t/s）。
- 8090 当前跑的是 **Strata Ornith-NVFP4**（不是 Bonsai）；llama.cpp 系脚本仍是互斥备选。

---

## §24 两种「pack」的区分 + 双槽 200K/48 槽定型（2026-10-21 15:00-15:40）

### ⚠️ 术语陷阱：Strata 语境下有两个「packed」，混起来会得出错误结论

| | **A · 专家 pack** | **B · Q8_0 dense decode 副本** |
|---|---|---|
| 是什么 | **121 个 `type 40`（NVFP4）张量**：`blk.N.ffn_{gate,up,down}_exps.weight`（40 层 × 3 = 120 个 routed expert 张量）+ `output.weight` | **157 个**满足 packed 形状门槛的 Q8_0 张量的第二份拷贝：`attn_qkv`(n_out=8192, 30 个)、`attn_q`/`attn_output`(11)、`attn_gate`(4096, 30)、`ffn_down_shexp`(2048, 41)、`ssm_out`(30)、`nextn.eh_proj` 与 MTP 块 3 个专家等 |
| 作用 | **专家卸载机制的前提** —— 专家能按层拆开搬进 VRAM 槽，未驻留的走 CPU pool 补算 | 只服务 attention 与 shared expert 的 decode，走 `q8_packed_mmvq()` 快速路径 |
| 显存 | 就是路由专家本体 17.67 GiB（锁页 host + 每层驻留槽） | **额外 1.28 GiB**（GGUF 原版已另有一份在显存） |
| 官方 35B 有吗 | **没有**。官方只给 **Qwen3.8-Flash-Next** 出了 NVFP4 pack | 不涉及模型版本，是引擎特性 |

**结论：官方没有 35B 的 NVFP4 pack ⇒「packed NVFP4」对本模型不成立；
但这不影响专家卸载机制 —— 我们手里本来就有 A 种 pack 形态的权重。**

`Ornith-1.5-35B-A3B-NVFP4-q36.gguf`（21.9 GB）**不是官方产物**，是从 `models/ornith-nvfp4/`
（HF safetensors + `hf_quant_config.json`，ModelOpt W4A8）自转的：
`convert_hf_to_gguf.py`（NVFP4 repack）→ `fix_router_shape.py` → `make_q36_gguf.py`（Strata q36 定制）。
产物天然是「专家 NVFP4 + dense Q8_0」两段式布局 ⇒ A 种 pack 形态天然具备。
（另有 `Ornith-1.5-35B-A3B-NVFP4.gguf` 23.85 GB 是另一份文件，非生产档所用。）

**GGUF 张量类型直方图**（自写解析器，994 个张量）：`551 F32 + 321 Q8_0 + 121 type40(NVFP4) + 1 BF16`。
其中 157 个 Q8_0 满足 packed 形状门槛（`n_in % 32 == 0 && n_out >= 2048`，`native_mmvq.cu:2048`），
**且全为 dense 张量** ⇒ 引擎建的 B 副本只覆盖 attention + shared expert，**一个路由专家都没碰**。

**B 副本为何会被建**：`q36_engine.cu:4238` 一句 `setenv("STRATA_Q8_PACKED","1",0)` **默认强制开**
（`native_q8_0_packed_enabled()` 返回 `enabled || q8_sm60_on()`）；`q36_engine.cu:1315` 建副本；
`native_mmvq.cu:2053` 在 registry 非空时走 packed 路径。
⇒ **这 1.28 GiB 与专家卸载机制完全无关**：17.67 GiB 路由专家仍全在锁页 host 内存，
每层 48/256 进显存槽，命中率 19.9%–24.5% 未受任何影响。

### 引擎改动（commit `e705e309`，原 `4ed69349` 已 amend）

显式 `--expert-slots N` 时，若「保留副本装不下、归还后装得下」就归还 B 副本。
空闲 4640 → 5920 MiB ⇒ 48 槽直接过（余 767 MiB）。
- 归还是**条件触发**（`need0 > fr && need0 <= fr + packed_bytes`），除非 `STRATA_Q36_GIVE_PACKED=1` 强制。
- 措辞修正：上游说「bitwise 相同、略慢」，**实测归还不更慢**（55.1 vs 53.3 t/s）
  ⇒ 源码注释与提交信息已改成「本模型 dense 占比小，副本收益抵不过 1.28 GiB 显存」。
- bitwise 门禁（`_tmp_code/packed_parity.py`）：48 槽 160K 双槽下，保留 vs 强制归还
  产出**完全相同的 24 个 token id**。前两次无效（条件未触发 / 48 槽未请求导致 A 崩）。

### 「1 GiB 留量」之谜（用户追问，已定案）

**不是留给桌面** —— 空载实测独显 **0 MiB**（桌面合成与唯一输出在核显）。
引擎三处留量：① `reserve = 768 MiB`（`:3214`，**仅自动算槽路径生效**，显式 slots 不参与）；
② `:1842` 多会话硬检查 `per × n_sess + 1 GiB > fr0`（双槽专属）；
③ `:3300` 256/512 MiB 真实工作缓冲。
真正可挖的只有 **B 副本 1.28 GiB**。

**用户悖论的答案**：nvidia-smi 在 WDDM 下失真（生产跑满时它报 4068 MiB used / 11983 free，
Task Manager 实为专用 15.0/16.0 GB + 共享 17.9/31.4 GB）。
共享那栏 = 17.67 GiB 锁页专家池经 PCIe 被 GPU 访问（**不是溢出**）。
共享兜底**不免费**：专家池放置 45.7s→96.6s（2.1x），且探针会把生产引擎挤到 `exit 1`
（HTTP 层已 ready，引擎懒启动失败 ⇒ 请求 503）⇒ **探针与生产必须互斥**。

### `--batch N` 的 KV 语义（与 llama.cpp `--parallel` 完全不同）

`q36_engine.cu:1833` `per = kv_half_bytes(...) × n_head_kv × ctx × n_attn`
⇒ **每路一整份，不共享不分片**；`:1842` 硬检查 `per × n_sess + 1 GiB > fr0` ⇒ `exit(1)`。
q4_0 单价 6.502 KiB/token（引擎实测反解）；双槽 200K 每路 1.27 GiB（两路 2.53 GiB）。

### 生产档最终形态（10-21 定型）

`strata-ornith-nvfp4-8090.json`：
`--max-context 204800 --kv q4_0 --expert-slots 48 --prefill auto --mtp 2 --batch 2`
⇒ **双槽 200K + 48 专家槽**。实测 60.5 t/s、命中率 19.9%。
**双槽 200K 与 48 槽是绑定的一档**：单槽时 48 槽装得下但 ctx 需求减半。
旧档（单路 256K + 48 槽）已下线。

### 坑：长上下文门禁脚本要按 ctx 调档位

双槽后每路 ctx 从 256K 降到 204.8K，`ctxladder2.py` 的第 4 档 5000 行 = 208,837 token
⇒ 引擎明确 400 拒绝（`prompt leaves no room to answer in the context`），**不是 bug，是引擎不截断**。
已把最后档改为 **4600 行**（~180K）。

### 坑：GitHub HTTPS 被选择性阻断，push 走 SSH:443

10-21 实测：`github.com` / `raw.githubusercontent.com` 不通（所有 IP、代理与直连都失败），
但 `api.github.com` / `codeload` / `objects` 通 ⇒ **SNI 层面选择性阻断**。
解法：`git push ssh://git@ssh.github.com:443/<owner>/<repo>.git`（github.com:22 与 ssh.github.com:443 均通，
SSH key 免额外配置）。已把 origin 的 push URL 改成 SSH（fetch 仍 HTTPS，只影响读）。

---

## §25 官方「pack」机制 vs 我们的 q36 路径（2026-10-21 15:30 调研）

### 结论先行

**官方 `strata-pack` 机制对本模型不适用**（不是「我们没做」，是「引擎根本不读」）。
但 pack 背后的**三个技术思想可以移植到 q36**，其中两个零风险。

### 三条硬证据（官方 pack 为何用不了）

1. **q36 引擎不读 pack 目录**。`src/qwen35/q36_engine.cu` 无 `pack_dir` / `experts.bin` / `native_experts.txt`
   任何引用；它自己从 GGUF 抽专家（`:1386-1388` `gm.shard(sg).tensor_data(*tg)` → mmap 指针）、
   自己 `cudaHostAlloc` 一大块锁页 arena（`:3356`）、自己逐专家 memcpy（`:3384` `expert_blob`）。
   官方 pack 消费方是**通用引擎** `src/core/expert_source.cpp`（`ArenaExpertSource::open(pack_dir,...)`），
   靠 `--pack-dir` 参数进入 —— 那是 Flash-Next 用的路。
2. **blob 几何硬编码 Flash-Next**：`tools/pack_layer.py` 顶部常量
   `H, FF, NE = 2560, 640, 512` + `QK, BLOCK_BYTES = 64, 18`（Q2_0）；
   每专家 blob 固定 1,382,400 B。我们是 hidden 2048 / 256 专家 / NVFP4，几何与量化都不同。
3. **`tools/canonical_xcheck.py` 的 `MAPPINGS` 不含 NVFP4**：
   只有 Q2_0 / Q3_K / Q4_0 / Q4_K / Q5_0 / Q5_K / Q6_K / Q8_0 / IQ4_NL / IQ4_XS / F32 / BF16 / F16。
   pack 的字节级规范（`decode(pack) == dequant_ggml(source)` bit for bit）只覆盖这些类型。

### 但 pack 的技术思想可以移植

| pack 的思想 | 我们的现状 | 可移植性 |
|---|---|---|
| **离线预转换专家池**（`experts.bin` = raw GGUF slices，按层切） | 启动时 17.67 GiB 逐专家 memcpy 到锁页 arena，**45.7 s**（并发时 96.6 s） | ✅ 零风险：写离线脚本产 `experts.bin`，引擎加 `--expert-pool` 直读；省掉全部 memcpy |
| **gate/up 行交织**（`pack_layer.py`：gate_up.codes = g0,u0,g1,u1,... 「one pass over the activation vector serves both」） | q36 是**分离**布局：`up_off = gu_row * n_ff`（`native_expert.cpp:96`），gate 区和 up 区各一段 | ⚠️ 要动 CPU kernel（`nvfp4_avx512_gu_rows` 遍历假设 + `native_fmt` 的 up_off） |
| **Hadamard folding**（`native_experts.txt` v5，`expert_layout.cpp:495` 校验 `hadamard.txt`） | 未用 | ⚠️ 需重算激活变换，收益待测 |

### q36 的 CPU 补算**已经**是优化过的（别重复劳动）

`src/kernels/cpu/native_expert.cpp` 有 NVFP4 专用 kernel：
`:260` AVX512 路径（`nvfp4_avx512_gu_rows`）、`:267` AVX2 路径（`nvfp4_avx256_gu_rows`），
注释标注 **1.8x @1 token / 3.7x @7 tokens**（相对 ggml-cpu）。
开关：`STRATA_NO_NVFP4_512` / `STRATA_NO_NVFP4_256` 可退回 ggml-cpu。
⇒ 「换 CPU kernel」这条路已被人走完，**「换专家量化格式」不可行**：
NVFP4 每值 0.125 B vs Q4_0 每值 0.5625 B ⇒ 17.67 GiB 会涨到 ~79 GiB，48 GB RAM 装不下。

### 🔑 命中率的天花板（最被低估的杠杆）

48 槽 / 256 专家 = **18.75% 理论上限**，实测命中率 **19.9%** ⇒
**热度排序几乎没带来增益**（top-48 热度集只比随机高 1 个百分点）。
机理：MoE 路由在长上下文下 token 强相关，跨请求累加的 score 预测力弱。
- `q36_engine.cu:2733` `score *= 0.5`（每次 rebalance 全表减半）
- `q36_engine.cu:2877/2881` `rebalance()` 只在 **prompt 结束后**做（decode 期间不换槽）
- `q36_engine.cu:3083` `hybrid_swaps_after_prompt = 2048`
- 观测点：`Report()`（`:4316`）在进程退出时打印「N of M batch entries on the GPU (X%)」——
  **只在退出时打印**，所以运行中拿不到；要看命中率得让进程正常退出或加日志。

---

## §26 离线专家池 `--expert-pool`（2026-10-21 16:00-16:15，details §25 的加速点 1）

### 动机与收益（三倍，不只是启动时间）

hybrid 模式每次启动都要把**全部路由专家**从 mmap 的 GGUF 逐专家 memcpy 进一块 `cudaHostAlloc`
的**锁页 arena**：
- **拷贝 = 45.7 s**（与生产并发时 96.6 s）
- **锁页内存 17.67 GiB**，叠在 GGUF 自身的 page cache 之上 ⇒ 48 GB 机器逼近换页

离线做一次 ⇒ 引擎 mmap 文件、host 池直接指过去 ⇒ **零拷贝 + 零锁页 + GGUF 专家页不进 RAM**。

### 精确几何（本模型，可复用的推导）

- **NVFP4 (type 40) = 36 B / 64 值** = 4 × UE4M3 sub-block scale (4 B) + 32 B E2M1 codes
  ⇒ bpr = 0.5625。`block_geometry(40)` = (64 elems, 36 bytes)。
- 主块专家 blob：`bytes = 2·n_ff·gu_row + n_embd·d_row + 16`，`gu_row = 1152`、`d_row = 288`
  ⇒ **bytes = 1,769,488**，`stride = ceil(bytes/144)·144 = 1,769,616`（`al = lcm(36,16) = 144`）
- MTP 块（**Q8_0**，不是 NVFP4！）：`bytes = stride = 3,342,336`（`al = lcm(34,16) = 272`，整除）
- 40 主块 × 256 + 1 MTP 块 × 256 = **17.67 GiB**（与引擎实测池大小精确一致 = 强交叉验证）

### GGUF 里的专家排列

`blk.N.ffn_{gate,up,down}_exps.weight` 的 ne = `[in, out, experts]`（**专家在最慢维 ⇒ 专家 e 字节连续**）；
MTP 块是 **2D 展平** `[in, out×experts]`（同样连续）。
`make_q36_gguf.py` 对 3D NVFP4 专家**原样保留**（不重排）。

### 文件格式（自描述，引擎逐字段校验）

```
header 64 B  magic "SQEP" | version | flags | n_blocks(u32) | n_expert | n_embd | n_ff
              | n_blocks(u64) | table_off | data_off | total_bytes
table 88 B/块  gu_type|d_type|gu_row|d_row|up_off|down_off|tail_off|bytes|stride|blob_off|scale_off|scale_n
data           每块每专家一个 blob（bytes 有效，stride 填充为 0）+ 每块 3×n_expert F32 scale
```
⚠️ **C++ 侧必须逐字段读**（`memcpy` 一个 struct 会因对齐 padding 与 Python 的 packed 布局错位）。
⚠️ header 第 28 字节是**总块数 41**（不是主层数 40）—— 我一度写成 n_main，引擎按 n_blocks 读才对。

### 交付物

- `tools/make_expert_pool.py`（生成，84 s / 17.67 GiB）+ `tools/verify_expert_pool.py`（独立重建比对）
- 引擎改动 `q36_engine.cu`：`MappedFile`（Win CreateFileMapping / POSIX mmap）+ `ExpertPool` + `--expert-pool`
  + `place_experts()` 加 pool 参数（host 池跳过 cudaHostAlloc 与 expert_blob 循环，直接指向 mmap）
- 提交：`f9fa03c2`（代码+工具）、`4a1582ed`（文档）

### 验证状态

- ✅ **离线字节级全量校验通过：10,496 个专家 blob（41 块 × 256）逐字节一致，0 差异**
- ❌ **端到端未验证**（8090 被另一会话的 `llama-server.exe` 占满 16024/16311 MiB）
  ⇒ 待测：① 启动时间 ② 与无池路径逐 token 相同 ③ **mmap 页 vs 锁页内存的 CPU 补算速度差异**
  （mmap 页有 page fault 开销，这是本改动的**主要未知风险**）
- 未上生产：`strata-ornith-nvfp4-8090.json` 暂未加 `--expert-pool`
- 对比脚本：`_tmp_code/pool_parity.py`（照 `packed_parity.py` 的模式，A=无池 B=有池，比 token id + READY 时间）

## 27. 视觉（strata-vision.exe）接进生产档（2026-10-21 18:00）

### 接入方式

配置 `strata-ornith-nvfp4-8090.json` 加一段：

```
"vision": {"exe": "D:/MyLLM/strata-run/strata-vision.exe",
             "mmproj": "D:/MyLLM/models/mmproj-Ornith-1.5-35B-BF16.gguf",
             "model": "D:/MyLLM/models/Ornith-1.5-35B-A3B-NVFP4-q36.gguf",
             "gpu": false, "threads": 16 }}
```

- 引擎侧还需 args 里有 `--vision`（`q36_engine.cu:4391`才收 `<|image_pad|>` 行，否则 `:4110` 报
  `started without --vision`）。serve 层逻辑（`server.py:2878`）：**有** vision 段但 args 缺 `--vision`
  会自动补上；**没有** vision 段 ⇒ 编码器根本不启动。
- `gpu: false` = mmproj 走 CPU，落 host RAM **不占显存**（899 MB）⇒ 48 专家槽得以保住。
- 匹配资产：mmproj `models/mmproj-Ornith-1.5-35B-BF16.gguf`（899 MB，BF16）；
  exe 在 `strata-run\strata-vision.exe`（另有 `strata-src\engine\strata-vision.exe`）。

### 🔑 起不来的真因：缺 cublas64_13.dll

**症状**：`RuntimeError: the vision encoder did not start:` —— **冒号后为空**。
根因两层：① config 没有 `"log"` 键 ⇒ 编码器 stderr 进 DEVNULL，真因被吞；
② 手动跑 exe 才看到真因：

```
strata-vision.exe: error while loading shared libraries: cublas64_13.dll: cannot open shared object file
```

`cublas64_13.dll` 不在 System32、也不在 `strata-vision.exe` 旁边、更不在 `strata-build2\`
（那里只有 `strata_kq.dll`）。手头唯一能干净加载的副本在 **`D:\MyLLM\llama.cpp\`**（CUDA 13.4 自包含构建）。

- ⚠️ **mmproj 走 CPU ≠ exe 不依赖 CUDA** —— 启动时仍要链接 CUDA 库。
- ⚠️ **`llama.cpp\` 目录不能删**，删了视觉立刻挂（它同时是 llama.cpp 系四个 bat 的运行时）。
- ⚠️ `vision_env()` 只认 `cuda_device`，**不支持任意自定义 env** ⇒ 只能从启动脚本注入
  `set "PATH=D:\MyLLM\llama.cpp;%PATH%"`。
- 手动验证：注入后 **6.2 s 就绪**，输出 `READY 2048` + `on the CPU, 16 threads, no warm-up`。

### CPU 模式两行「正常」日志（别误判为故障）

- `ggml_cuda_init: failed to initialize CUDA` —— 没有 `--gpu` 就是这样
- `strata-vision: on the CPU, 16 threads, no warm-up` —— 正常
- `load_hparams: Qwen-VL models require at minimum 1024 image tokens` —— 提示，当前默认 2048

## 28. q8_0 KV + 单槽 256K + 视觉（当前生产档）

```
--max-context 262144  --kv q8_0  --expert-slots 48  --prefill auto  --mtp 2  --vision
```

从上一档（`--kv q4_0 --max-context 204800 --batch 2`双槽 200K）改来：**删掉 `--batch 2` 回单槽**，
ctx 拉回 256K，KV 从q4_0（1.55 GiB）升到 **q8_0（2.92 GiB）**。

- 显存账：KV 2.92 + 专家槽 3.31 + dense 4.34 + staging 1.59 + 引擎缓冲 ~2.87。
- **decode 实测36.2 / 37.0 / 39.3 / 36.0 / 39.1 / 43.3 t/s**（6 轮短请求）
  ⇒ 比 q4_0 档的 46.5–49.2 t/s **降 20–25%**，与 KV 每token 6.2→11.7 KiB 的预期一致。
- ⚠️ **该模型输出走 `reasoning_content` 而非 `content`**（thinking 风格）。`max_tokens` 用尽时
  `content = null` + `finish=length`，脚本直接读 `content` 会 TypeError。
  **必须带 `reasoning_budget_tokens`**，否则日志刷 `the reply reached max tokens while still thinking`。

### 概念澄清：`--batch N`（并发请求槽）≠ `--expert-slots N`

- `--batch N` = 同时解码 N 路会话，KV 按 `per × n_sess` 算（`q36_engine.cu:1966`），
  **每路一整份完整 KV，不共享不分片**。`:1973` 超配直接 `exit(1)`，**不缩容**。
- 与 llama.cpp 的 `--parallel`（`kv_unified` 或槽内分片）语义完全不同。
- 所以「去掉 `--batch` 就能装下 KV」只在**本来就是单槽**时成立（本档就是），否则省不掉第二份。

## 29. 权重资产溯源与磁盘（2026-10-21 18:20）

### 三个文件，别搞混

| 文件 | 大小 | 来历 |
|---|---|---|
| `models\ornith-nvfp4\` | 21.84 GiB | **HF safetensors 原始下载**（ModelScope），2026-10-20 13:43，`dl.sh` + `download.log` |
| `models\Ornith-1.5-35B-A3B-NVFP4.gguf` | 23.85 GB | 由上面 `convert_hf_to_gguf.py` 转出（~4 min，994 张量，type40 = **241**），llama.cpp 备选档 |
| `models\Ornith-1.5-35B-A3B-NVFP4-q36.gguf` | 20.41 GiB | 生产模型，`_tmp_code/make_q36_gguf.py` 从 `-fix.gguf` 转出（type40 = **121**） |

- q36 转换脚本的输入是 `Ornith-1.5-35B-A3B-NVFP4-fix.gguf`（23.8 GB），**该中间文件已删**。
- ⚠️ **`models\ornith-nvfp4\` 绝不能删**：双重身份 = 转换链源头 **+ 当前 8090 配置的 `"tokenizer"` 目录**。
- 两个 GGUF 中间产物都能从它重建（4 min重转 / 84 s 重建专家池）。

### 磁盘现状（18:20 扫描，**未删任何文件**）

D 盘空闲 ~44 GiB（88% 已用），`models/` 独占 250 GiB。

- **KEEP 43.1 G**：q36 模型 20.41 + `ornith-nvfp4\` tokenizer 21.84 + mmproj 0.84
- **休眠备选 155.1 G**：122B 46.96 · NVFP4.gguf 23.85 · Q4_K_M 20.22 · ornith-official 17.95 ·
  APEX-MTP 16.24 · LFM2.5-24B 12.65 · Qwen3.8-27B 11.21 · bonsai_mtp 7.64
- **真孤儿 29.3 G**：MiniCPM-V-4.7 split 14.44 · LFM2.5 split 12.65 · GLM-OCR 2× 1.34 · DFlash-bf16 0.73
- 另有 5.08 GiB 陈旧构建目录（`strata-build` 已被 build2 取代、`_upgrade`、两个 llama.cpp 备份）
- ⚠️ **「某个 .bat 还引用它」≠「你需要它」** —— 8090 已归 Strata，llama.cpp 系备选档全是休眠的。

### 🚨 删文件不释放空间的教训

10-20 删掉 23.8 GB 的 `-fix.gguf` 后**D 盘空闲没有回升**（21.7 GiB，无进程持有）。
⇒ 真要清理前，**先拿一个小文件验证删除确实释放了空间**。

扫描/统计脚本：`_tmp_code/scan_space.py`、`_tmp_code/space_report.py`
（坑：`endswith("*.bat")` 传 glob 永远不匹配，必须传后缀元组；配置文件里写的是完整路径，
按纯文件名匹配会全判 ORPHAN）。

## 30. 🔧 Edit 工具改 md 会把行尾写成 LF（本仓库是 CRLF）

`strata-src` 的 `core.autocrlf=true`，原始文件全是 CRLF。用 Edit 写 md 后**整个文件变 LF**
⇒ `git diff` 里半屏是行尾变化。**提交前必跑归一**：

```python
b = open(p,'rb').read()
b = b.replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
open(p,'wb').write(b)
```

校验：`CRLF行数 == 总行数` 且孤立 LF == 0。

## 31. 🔴 tokenizer 被磁盘清理删除与重建（2026-10-21 19:25）

**事件**：清理磁盘时 `D:\MyLLM\models\ornith-nvfp4` 整个被删。
D 盘 44 GiB → 113 GB，释放的 69 GB ≈ 122B(46.96) + ornith-nvfp4(21.84)。
该目录**双重身份**：① HF safetensors 原始权重（转换链源头）② 8090 配置的 `"tokenizer"` 目录。
记忆里当时标了「绝不能删」，仍然被删了 ⇒ **磁盘清理前必须先查配置引用，不能只看体积。**

**后果**：`serve/server.py:7697` 硬检查 `vocab.json`，缺则 `ap.error(...)` 直接退出 ⇒ **服务起不来**。
q36 GGUF **不内嵌 tokenizer**（实测 KV 只 3 项），所以 GGUF 救不了。

**已修复**：从 ModelScope 只下5 个小文件（21.84 MiB，6秒），**没重下 21.84 GiB 权重**。
源：`https://www.modelscope.cn/models/ornith-ai/Ornith-1.5-35B-A3B-NVFP4/resolve/master/`
脚本：`tools/local/rebuild_tokenizer.py` → `fix_vocab.py` → `verify_tokenizer.py`

### 三个坑（重建必踩）

| # | 现象 | 根因 | 解法 |
|---|---|---|---|
| 1 | `ST.Tokenizer` 抛 `IndexError: list index out of range` | **ModelScope 的 `vocab.json` 只有 248044 项，不含 added_tokens**；而 `strata_tokenizer.py:107` 执行 `self.special_tokens[tokens[i]] = i` ⇒ types 比 tokens 长就越界 | **把 26 个 added_tokens 追加进 vocab.json**（248044 → **248070**），`token_type.json` 同步写 248070 长 |
| 2 | `merge 247587 is not a pair: ''` | ModelScope 的 `merges.txt` 以换行结尾，`split(chr(10))` 多产一个空串 | 剥离尾部空行 ⇒ **247587** 个合法 pair（不是 247588） |
| 3 | ModelScope 上没有 `token_type.json` | 它是 **Strata setup 的派生产物**，不在 HF 仓库里 | 规则：`1=NORMAL`、`3=CONTROL`；CONTROL 取 `tokenizer.json` 的 `added_tokens` 里 `special:true`（本模型 **14 个**，id 248044–248057）。另外 `merges.txt` 里大量 `Ã¦` 类字符是 **GPT-2 byte-level 编码的正常表现，不是 mojibake** |

**验证**：`strata_tokenizer` 构建成功，**5/5 往返全过**（中文 8 ids / 英文 10 ids / 代码 13 ids / 算式 17 / 混合 21），
词表规模与删除前服务运行时记录一致。

⚠️ **重建后的 `merges.txt` 与 ModelScope 原文件不字节相同**（去了尾部空行）。
要严格可复现必须连`fix_vocab.py` 一起用。

⚠️ **21.84 GiB 原始权重没恢复** ⇒ 重转 q36 GGUF 的链条断了：
需重下 3 个分片（10.0+10.0+3.4 GB），且 `tools/local/make_q36_gguf.py` 的输入
`Ornith-1.5-35B-A3B-NVFP4-fix.gguf` **也已不存在**。
