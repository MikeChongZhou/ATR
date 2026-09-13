# 本地会议录音

这是一个 Windows 本地托盘录音工具。程序启动后默认显示英文界面，只显示托盘图标，右键图标可以打开菜单：

- 🔴 Start Recording / ◼ Stop Recording
- Text Window
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
- 录音期间 MP3 数据保存在内存中，不持续写入硬盘。
- 录音期间不再生成临时 WAV、speaker WAV 或临时 TXT 文件。
- 点击 ◼ Stop Recording 并确认保存后，才会把内存中的 MP3 和当前文本一次性保存到用户选择的目录。
- MP3 和文本文件默认使用开始录音时的年月日时分作为文件名，例如 `20260912_1056.mp3` 和 `20260912_1056.txt`。
- 点击 Text Window 会打开实时转写窗口；没有录音时窗口内容为空。
- 文本窗口底部提供复制按钮，可以把窗口里的文本复制到剪贴板。
- 点击 Start Recording 后会先选择本次转录语言和是否开启 Auto Screenshot；转录语言默认 English。
- 实时转写使用 `faster-whisper`：English 使用英文蒸馏模型 `distil-small.en`，每 5 秒处理一次，实时 `beam_size` 为 5，`condition_on_previous_text=True`；中文使用多语言模型 `small` 并固定 `language="zh"`，每 10 秒处理一次，实时 `beam_size` 为 5，`condition_on_previous_text=True`，并加入简体中文和中文标点提示词；后台复核转写当前暂时关闭。
- 中文模式会在转写结果返回后尝试使用 OpenCC 做繁体转简体；如果尚未安装 `opencc-python-reimplemented`，程序仍可运行，但不会强制繁转简。
- 会议纪要会根据实时转写文本生成主要讨论和 action items。
- 开始录音时会在程序或 exe 同级目录下创建一次 `screen\YYYYMMDD_HHMM\` 目录，同一次录音期间的手动和自动截图都保存到这个目录，截图文件名为 `YYYYMMDD_HHMMSS.jpg`。
- 没有录音时，手动 Take a screenshot 会按当前年月日时分创建一个截图目录并保存截图。
- 点击 Auto Screenshot: Off 会开启自动截屏，菜单文字变为 Auto Screenshot: On；开启后会立即截屏一次，之后每 30 秒截屏一次，不再缩小图片或比对画面变化；再次点击会关闭自动截屏。
- 点击 Stop Recording 时 Auto Screenshot 也会自动停止。
- About 窗口说明本软件为开源软件，遵循自由使用原则，显示当前版本号，并提供 English / 中文 界面切换。

注意：因为录音期间 MP3 只保存在内存中，如果程序崩溃、被强制结束或电脑断电，尚未保存的录音会丢失。

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
