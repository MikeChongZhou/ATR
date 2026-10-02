# 本地会议录音

这是一个 Windows 本地托盘录音工具。程序启动后默认显示英文界面，只显示托盘图标，右键图标可以打开菜单：

- 🔴 Start Recording / ◼ Stop Recording
- Text Window
- Offline Transcript
- System Settings
- Take a screenshot
- Auto Screenshot: On / Auto Screenshot: Off
- About
- Exit

## 功能

- 点击 🔴 Start Recording 开始录音；录音后同一菜单项变为 ◼ Stop Recording，再点击即可停止录音。
- 不再提供单独的 Stop Recording 菜单项；点击托盘图标本身也不会开始或停止录音。
- 录音采样率为 48000 Hz，单声道，输出格式为 MP3。
- 扬声器默认录制 Windows 默认扬声器的本地回放声音。
- 麦克风会先检测是否有其他 app 正在使用默认麦克风；只有检测到其他 app 正在使用麦克风时，本程序才会打开麦克风录音。
- 麦克风音频会和扬声器音频做轻量相关性比较；如果麦克风音频高度类似扬声器漏音，就不会混入 MP3，也不会标为 `Me`。
- 通过漏音过滤的麦克风音频会混入 MP3，并在实时转录中标为 `Me`；扬声器音频在实时转录中标为 `Others`。
- 点击 ◼ Stop Recording 或 Exit 会停止录音线程并关闭本程序打开的麦克风录音。
- 录音时底层每 0.05 秒读取一次声卡音频，内部拼成 0.5 秒音频块再送给 MP3 编码和实时转写。
- 录音采集线程会尽量提高优先级；实时转写、会议纪要和自动截屏线程会降低优先级，减少对录音采集的影响。
- 录音开始时会在程序或 exe 同级目录的 `output` 目录下创建 `.mp3.partial` 和 `.txt.partial` 文件。
- 录音期间 MP3 会边录边编码并持续写入 `.mp3.partial`，不再把整段录音长期保存在内存中。
- 实时转写每次追加新文本后会刷新 `.txt.partial`，降低程序异常退出时丢失文本的风险。
- 录音期间不再生成临时 WAV 或 speaker WAV 文件。
- 点击 ◼ Stop Recording 后会停止录音和转写，把 `.partial` 文件改名为正式 `.mp3` 和 `.txt` 文件，并弹出保存路径通知；不再弹出保存位置选择框。
- MP3 和文本文件默认使用开始录音时的年月日时分作为文件名，例如 `20260912_1056.mp3` 和 `20260912_1056.txt`。
- 点击 Text Window 会打开实时转写窗口；没有录音时窗口内容为空。
- 文本窗口底部提供复制按钮，可以把窗口里的文本复制到剪贴板。
- 点击 Offline Transcript 打开离线转写窗口。该窗口可以导入音频文件，选择 English / Chinese、切片时长、重叠时长、beam size、Whisper engine、VAD、`condition_on_previous_text`、最低音量阈值、中文简繁转换、是否播放每个转写片段、是否记录每段转写耗时和初始提示词，然后点击 Start 开始离线转写。
- 离线转写窗口提供 Audio 按钮行：Import、Play、Pause、Stop、-15s、+15s；Transcript 按钮行：Start、Pause、Stop、Save As、Generate Minutes。
- 离线转写会分别显示音频播放进度条和转写进度条，并显示已处理时长、开始时间、完成时间和总用时；Generate Minutes 会对当前离线转写文本生成会议纪要并追加到文本窗口；Save As 会把已经完成转写进度对应的音频片段和当前转写文本一起保存，默认文件名使用 `YYYYMMDD_HHMM`；窗口关闭时会停止离线播放和离线转写子进程。
- 勾选 Offline Transcript 的 `Log chunk timing` 后，每个转写片段完成时会在文本窗口和 `local_meeting_recorder.log` 中记录音频时间段、Whisper 耗时、RTF 和实时积压估算，便于判断当前参数是否会越转越慢。
- 离线转写和实时录音互斥：实时录音中不能启动离线转写；离线转写正在运行时不能开始实时录音。
- 离线转写复用实时转写的 Whisper 子进程和切片处理方式，便于用离线文件测试实时转写参数。
- 点击 System Settings 可以调整实时转写参数和自动截屏间隔，包括转写采样率、Whisper device / compute type / CPU threads / workers、VAD、VAD 静音时长、最低 RMS、实时转写输入队列大小、音频采集块、录音读取间隔、录音内部拼块、Auto Screenshot 间隔秒数，以及 English / Chinese 各自的模型、模型目录、语言、切片长度、重叠时长、beam size、`condition_on_previous_text`、基础 prompt 和简繁转换设置。
- 点击 Start Recording 后会先选择本次转录语言、是否开启 Auto Screenshot，并可以输入本次辅助识别 prompt；该 prompt 会按语言保存，下次开始实时转录时自动带出，用户可以修改或继续沿用。
- 实时转写使用 `faster-whisper`：English 使用英文蒸馏模型 `distil-small.en`，每 5 秒处理一次，实时 `beam_size` 为 5，`condition_on_previous_text=True`；中文使用多语言模型 `small` 并固定 `language="zh"`，每 10 秒处理一次，实时 `beam_size` 为 5，`condition_on_previous_text=True`，并加入简体中文和中文标点提示词；后台复核转写当前暂时关闭。
- 中文模式会在转写结果返回后尝试使用 OpenCC 做繁体转简体；如果尚未安装 `opencc-python-reimplemented`，程序仍可运行，但不会强制繁转简。
- 会议纪要使用 System Settings 的 Summary 页配置的本地 LLM；如果本地 LLM 未启用、不可用或超时，程序不会再生成低质量规则版会议纪要。录音停止并保存 MP3/TXT 后，会议纪要会进入后台任务，不会阻塞 Stop Recording 和保存通知。
- 开始录音时会在程序或 exe 同级目录下创建一次 `screen\YYYYMMDD_HHMM\` 目录，同一次录音期间的手动和自动截图都保存到这个目录，截图文件名为 `YYYYMMDD_HHMMSS.jpg`。
- 没有录音时，手动 Take a screenshot 会按当前年月日时分创建一个截图目录并保存截图。
- 点击 Auto Screenshot: Off 会开启自动截屏，菜单文字变为 Auto Screenshot: On；开启后会立即截屏一次，之后每 30 秒截屏一次，不再缩小图片或比对画面变化；再次点击会关闭自动截屏。
- 点击 Stop Recording 时 Auto Screenshot 也会自动停止。
- 程序不再自动判断会议是否结束；录音结束由用户点击 ◼ Stop Recording、Exit，或 Windows 睡眠/关机通知触发。
- Windows 进入睡眠、待机或关机前，如果程序收到系统通知，会尽快停止录音和转写，把当前 partial 文件收成正式 MP3 和文本文件并保存到 `output` 目录。
- About 窗口说明本软件为开源软件，遵循自由使用原则，显示当前版本号，并提供 English / 中文 界面切换。

注意：录音和文本会持续写入 `.partial` 文件。正常停止、Exit、系统睡眠/关机通知都会把 partial 文件改名为正式文件；如果程序崩溃、被任务管理器强制结束或电脑直接断电，`output` 目录里仍可能保留 `.partial` 文件，可作为抢救数据使用。

如果 Stop Recording 没有正常恢复到 Start Recording，程序会把 Stop 流程的每一步写入程序或 exe 同级目录的 `local_meeting_recorder.log`，包括各线程 join 耗时、转写子进程关闭、partial 文件改名和状态恢复。

## 安装和运行

建议使用 Python 3.10 或更高版本。

```powershell
python -m pip install -r requirements.txt
python main.py
```

如果希望隐藏命令行窗口，可以双击 `run.bat`。

## 生成 exe

```powershell
.\build_exe.ps1
```

生成的程序位于：

```text
dist\LocalMeetingRecorder.exe
```

## Whisper 模型

第一次运行实时转写时，模型可能需要下载，耗时取决于网络和电脑性能。

也可以手动下载模型到项目目录：

```powershell
python -m pip install -U "huggingface_hub[cli]"
hf download Systran/faster-distil-whisper-small.en --local-dir .\models\faster-distil-whisper-small.en
hf download Systran/faster-whisper-small --local-dir .\models\faster-whisper-small
```

如果 `models\faster-distil-whisper-small.en` 存在，English 会优先使用这个本地模型目录；否则使用模型名 `distil-small.en`。

如果 `models\faster-whisper-small` 存在，中文会优先使用这个本地模型目录；否则使用模型名 `small`。

## 本地 LLM 会议纪要

最终会议纪要使用本机 OpenAI-compatible API。默认配置为托管 llama.cpp，并使用 `Qwen/Qwen3-0.6B-GGUF:Q8_0`。录音和转写保存完成后，程序会在后台启动或复用 llama server 生成会议纪要，并写回同一个 txt 文件。

长转写文本会自动分段总结：程序先按 `Chunk chars` 把 transcript 切成多个 chunk，逐段生成 chunk summary，然后再把 chunk summaries 合并成最终会议纪要。`Final merge max chars` 控制最后合并阶段最多送入多少字符，`Max output tokens` 控制每次 LLM 调用的最大输出长度。

如果使用 `-c 4096 -np 1` 启动 llama.cpp，建议把 `Chunk chars` 设置在 `5000-7000` 之间；如果使用 `-c 8192`，可以适当提高到 `9000-12000`。

托管模式会根据 Summary 设置自动启动 llama：

```text
Managed llama server: on
llama executable: 留空时自动查找 .\llm\llama.exe、.\llm\llama-server.exe 或 PATH 里的 llama
llama model ref: Qwen/Qwen3-0.6B-GGUF:Q8_0
llama context: 4096
llama slots: 1
```

如果开始新的录音、检测到其他 app 正在使用麦克风，或者 CPU/内存超过 Summary 页的暂停阈值，程序会停止托管的 llama 进程并暂停会议纪要；等电脑空闲后再恢复。

如果希望手动运行 llama.cpp，可以关闭 `Managed llama server`，然后启动：

```powershell
winget install llama.cpp
llama serve -hf Qwen/Qwen3-0.6B-GGUF:Q8_0 --host 127.0.0.1 --port 8080 -c 4096 -np 1
```

对应设置：

```text
OpenAI-compatible URL: http://127.0.0.1:8080/v1/chat/completions
Model: Qwen/Qwen3-0.6B-GGUF:Q8_0
```

如果使用 Ollama：

```powershell
ollama run hf.co/Qwen/Qwen3-0.6B-GGUF:Q8_0
```

对应设置：

```text
OpenAI-compatible URL: http://127.0.0.1:11434/v1/chat/completions
Model: hf.co/Qwen/Qwen3-0.6B-GGUF:Q8_0
```
