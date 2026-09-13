# 本地会议录音

这是一个 Windows 本地托盘录音工具。程序启动后默认显示英文界面，只显示托盘图标，右键图标可以打开菜单：

- Record
- Text Window
- Stop Recording
- Screenshot
- Auto Screenshot On / Auto Screenshot Off
- About
- Exit

## 功能

- 当前版本只录制 Windows 默认扬声器的本地回放声音，不会主动打开或录制麦克风。
- 录音采样率为 48000 Hz，单声道，输出格式为 MP3。
- 录音时底层每 0.05 秒读取一次声卡音频，内部拼成 0.5 秒音频块再送给 MP3 编码和实时转写，减少固定 0.5 秒大块读取造成短静音的概率。
- 录音采集线程会尽量提高优先级；实时转写和会议纪要线程会降低优先级，减少对录音采集的影响。
- 录音期间 MP3 数据保存在内存中，不持续写入硬盘。
- 录音期间不再生成临时 WAV、speaker WAV 或临时 TXT 文件。
- 点击 Stop Recording 并确认保存后，才会把内存中的 MP3 和当前文本一次性保存到用户选择的目录。
- MP3 和文本文件默认使用开始录音时的年月日时作为文件名，例如 `20260509_14.mp3` 和 `20260509_14.txt`。
- 点击 Text Window 会打开实时转写窗口；没有录音时窗口内容为空。
- 文本窗口底部提供复制按钮，可以把窗口里的文本复制到剪贴板。
- 实时转写使用 `faster-whisper`，默认模型为英文蒸馏模型 `distil-small.en`；实时文本默认每 3 秒处理一次，实时 `beam_size` 为 3，后台复核转写当前暂时关闭。
- 当前只录扬声器，因此实时转录的发言人标签显示为 `Others`。
- 会议纪要会根据实时转写文本生成主要讨论和 action items。
- 手动 Screenshot 会在程序或 exe 同级目录下创建 `screen\YYYYMMDD_HH\` 目录，截图文件名为 `YYYYMMDD_HHMMSS.jpg`。
- 点击 Auto Screenshot Off 会开启自动截屏，菜单文字变为 Auto Screenshot On；开启后会立即截屏一次，之后每 30 秒截屏一次，不再缩小图片或比对画面变化；再次点击会关闭自动截屏。
- 点击 Record 时会询问是否为本次录音开启 Auto Screenshot；点击 Stop Recording 时 Auto Screenshot 也会自动停止。
- 点击 Record 后，录音菜单不会变成暂停/继续；如需结束录音，请点击 Stop Recording。
- 未录音时点击 Stop Recording，会提示当前并未录音。
- About 窗口说明本软件为开源软件，遵循自由使用原则，并提供 English / 中文 界面切换。
- 点击 Exit 会停止托盘、录音线程和转写线程，释放内存后退出。

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
```

如果 `models\faster-distil-whisper-small.en` 存在，程序会优先使用这个本地模型目录；否则使用模型名 `distil-small.en`。

注意：`distil-small.en` 是英文语音识别模型。如果需要中文或中英混合转写，可以把 `main.py` 中的 `WHISPER_MODEL` 改为 `small`，并把 `WHISPER_LANGUAGE` 改为 `"zh"` 或 `None`。
