# 本地会议录音

这是一个 Windows 本地托盘录音软件。程序启动后默认显示英文界面，只显示托盘图标，右击图标可以看到：

- Record
- Text Window
- Stop Recording
- Screenshot
- Auto Screenshot On / Auto Screenshot Off
- About
- Exit

## 功能

- 点击“录音”后开始同时捕获 Windows 默认扬声器回放声音和默认麦克风声音，适合记录会议、视频通话、网课或播放器声音。
- 录音期间会以 48000 Hz 生成 MP3 临时文件，并复制一份音频降采样到 16000 Hz 后交给本地 Whisper 蒸馏模型同步转写文本。实时文本默认每 2 秒处理一段，后台核查转写默认每 15 秒处理一段，语言默认固定为英文。
- Whisper 转写会在录音开始时按需启动独立子进程，停止录音后子进程退出并释放模型内存；主托盘程序不会常驻持有 Whisper 模型。
- 实时转录会区分 `Me` 和 `Others`：麦克风音频标记为 `Me`，扬声器回放音频标记为 `Others`。同一来源连续说话时会合并在同一段里，句子单独换行。
- 文本窗口会根据已转录文本实时刷新会议纪要，包含主要讨论、action items，以及每个 action item 的负责人。负责人会优先从句子里的姓名或 `assigned to / owner / 负责` 等表达中提取；如果没有明确姓名，则回退为当前说话来源 `Me`、`Others`、`Team` 或 `Unassigned`。
- MP3 和文本的默认文件名使用启动录音时的年月日时，例如 `20260509_14.mp3` 和 `20260509_14.txt`。
- 点击“文本窗口”会打开实时转写窗口，窗口底部提供“复制文本到剪贴板”按钮。
- 点击“Screenshot / 截屏”会在程序或 exe 所在目录下创建并使用 `screen\YYYYMMDD_HH\` 目录，截图文件名为 `YYYYMMDD_HHMMSS.jpg`。
- 点击“Auto Screenshot Off”会开启自动截屏，菜单文字变为“Auto Screenshot On”；再次点击会关闭自动截屏并恢复为“Auto Screenshot Off”。
- 点击“录音”时会询问是否为本次录音开启 Auto Screenshot；选择是则启动自动截屏，选择否则关闭自动截屏，并同步刷新菜单文字。
- 录音时菜单项“录音”不会再切换为暂停或继续；需要结束时点击“停止录音”。
- 点击“停止录音”后会立刻停止录音采集，结束 MP3 编码，取消滞后的实时预览队列，等待录音期间已经在后台进行的核查转写完成尾段，然后在文本末尾追加本地生成的会议总结和 action items，再询问是否保存 MP3 和文本文件。保存框里会预填默认文件名，用户可以修改文件名和保存目录。
- 点击“停止录音”时 Auto Screenshot 也会自动停止，菜单文字恢复为“Auto Screenshot Off”。
- 未录音时点击“停止录音”，会提示“当前并未录音”。
- 未录音时点击“文本窗口”，窗口内容为空。
- 点击“关于”，会提示该软件为开源软件，遵循自由使用原则，并提供 English / 中文 两个语言勾选项。切换后会刷新当前界面语言。
- 点击“Exit / 退出”会停止托盘、录音线程和转写线程，清理临时目录并退出程序。

## 安装和运行

建议使用 Python 3.10 或更高版本。

```powershell
python -m pip install -r requirements.txt
python main.py
```

如果希望启动后没有命令行窗口，可以双击 `run.bat`。

## 打包成 exe

```powershell
.\build_exe.ps1
```

生成的程序在：

```text
dist\LocalMeetingRecorder.exe
```

## 说明

实时文本窗口会分为两层：`[Draft]` 先显示快速转写结果，`[Final]` 显示后台核查后的结果。后台核查每 15 秒处理一段音频，相邻两段保留 2 秒重叠；核查完成后会立即写入临时 txt。停止录音后最多等待后台核查 15 秒，超过后先弹出保存框，后台完成后会继续补写最终 txt。

实时转写使用 `faster-whisper`，默认模型为英文蒸馏模型 `distil-small.en`。第一次运行转写时，模型可能需要下载，耗时取决于网络和电脑性能。

也可以手工下载模型到项目目录：

```powershell
python -m pip install -U "huggingface_hub[cli]"
hf download Systran/faster-distil-whisper-small.en --local-dir .\models\faster-distil-whisper-small.en
```

如果 `models\faster-distil-whisper-small.en` 存在，程序会优先使用这个本地模型目录；如果不存在，会自动回退到 `models\faster-whisper-small`。这样可以避免 Windows 在 Hugging Face 默认缓存目录创建符号链接时出现 `[WinError 1314] A required privilege is not held by the client`。

如果仍希望让 Hugging Face 自动下载到系统缓存，需要在 Windows 中启用“开发人员模式”或以管理员权限运行；更推荐使用上面的 `hf download --local-dir` 命令手工下载到项目目录。

注意：`distil-small.en` 是英文语音识别模型。如果需要中文语音转写，请把 `main.py` 中的 `WHISPER_MODEL` 改回 `small`，并把 `WHISPER_LANGUAGE` 改为 `"zh"` 或 `None`。
