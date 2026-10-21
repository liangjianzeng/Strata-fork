@echo off
REM [!] Strata q36 Ornith-NVFP4 on port 8090 (server.py + strata.exe engine)
REM [!] config: D:\MyLLM\strata-ornith-nvfp4-8090.json (api_key + host inside)
REM [!] tier: --max-context 262144 --kv q8_0 --expert-slots 48 --prefill auto --mtp 2 --vision
REM [!] routed experts come from the offline pool (--expert-pool), mapped not copied in
REM [!] one request at a time (no --batch: N slots would need N full KV copies)
REM [!] vision encoder on the CPU (gpu:false) so its 899 MB mmproj leaves VRAM to the experts

netstat -ano | findstr /R /C:":8090 .*LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [!] port 8090 already in use - service probably running. Use stop_strata_8090.bat first.
    pause
    exit /b 1
)

REM [!] strata-vision.exe needs cublas64_13.dll, which is not in System32 and not beside the
REM [!] exe. llama.cpp\ holds the CUDA 13.4 self-contained build; without this the encoder dies
REM [!] with "error while loading shared libraries: cublas64_13.dll".
set "PATH=D:\MyLLM\llama.cpp;%PATH%"

REM [!] Warm the pool into the page cache first. Cold, the engine faults 17.67 GiB in
REM [!] during warm-up and starts about twice as slow as the pinned-arena copy. Reading it
REM [!] here costs ~7s at ~2.6 GB/s - 48 GB of RAM holds it - and takes that cost off the
REM [!] critical path: READY 56s -> 30s. Skipped if the file is already gone.
if exist D:\MyLLM\models\experts-q36.bin (
    echo [i] warming the expert pool into the page cache - 17.67 GiB, about 7s ...
    C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe -NoProfile -Command "$f=[IO.File]::OpenRead('D:\MyLLM\models\experts-q36.bin');$b=New-Object byte[] 16777216;while($f.Read($b,0,$b.Length)-gt 0){};$f.Close()"
)

echo [i] starting Strata Ornith-NVFP4 on 0.0.0.0:8090 ...
echo [i]   context 256K . q8_0 KV . 48 expert slots . vision encoder on CPU
echo [i] first load takes ~1 min (17.67 GiB of routed experts mapped from the offline pool)
echo [i] keep this window open - closing it stops the model
cd /d D:\MyLLM\strata-src
C:\Users\jianz\.workbuddy\binaries\python\versions\3.13.12\python.exe -m serve.server --engine strata --config D:\MyLLM\strata-ornith-nvfp4-8090.json --port 8090
echo [i] server exited rc=%errorlevel%
pause
