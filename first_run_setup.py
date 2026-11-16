"""Background preparation only: downloading never starts an inference server."""
import threading
from pathlib import Path
from downloads import download_file, DownloadPaused
from llama_runtime import ensure_runtime, SetupCancelled


class LocalModelSetup:
    def __init__(self):
        self.cancel = threading.Event()
        self.thread = None
        self.state = "idle"
        self.message = ""
        self.percent = 0
        self.path = None

    def update(self, percent, message):
        self.percent, self.message = percent, message

    def pause(self):
        if self.state == "running":
            self.message = "Pausing setup / 正在暂停"
        self.cancel.set()

    def start(self, directory, settings, preset, target_dir, completed=lambda path: None):
        if self.thread and self.thread.is_alive():return
        self.cancel.clear();self.state="running";self.path=None
        self.thread=threading.Thread(target=self.run,args=(directory,dict(settings),preset,target_dir,completed),daemon=True,name="first-run-local-model-setup")
        self.thread.start()

    def run(self,directory,settings,preset,target_dir,completed):
        try:
            ensure_runtime(Path(directory),str(settings.get('llm_server_executable','') or ''),
                progress=lambda message:self.update(0,message),cancelled=self.cancel.is_set)
            configured=str(settings.get('llm_server_model_path','') or '').strip()
            if configured:
                if not Path(configured).is_file():raise RuntimeError('Configured GGUF file is missing. Select a model in System Settings.')
                path=Path(configured)
            else:
                if not preset:raise RuntimeError('Choose a downloadable LLM preset in System Settings.')
                from huggingface_hub import HfApi,hf_hub_url
                self.update(0,'Finding default LLM model / 查找默认模型')
                info=HfApi().model_info(preset['repo_id'],files_metadata=True)
                candidates=[f for f in info.siblings if f.rfilename.lower().endswith('.gguf') and preset['quant'].lower() in f.rfilename.lower() and '-of-' not in f.rfilename]
                if not candidates:raise RuntimeError('No single-file GGUF matched the selected quantization.')
                file=min(candidates,key=lambda f:len(f.rfilename))
                lfs=file.lfs
                digest=(lfs.get("sha256") if isinstance(lfs,dict) else getattr(lfs,"sha256",None)) if lfs else None
                path=download_file(hf_hub_url(preset['repo_id'],file.rfilename,revision=info.sha),
                    Path(target_dir)/file.rfilename,cancelled=self.cancel.is_set,sha256=digest,
                    progress=lambda p,m:self.update(p,'LLM: '+m))
            if self.cancel.is_set():raise DownloadPaused()
            self.path=path;self.state='ready';self.update(100,'Local LLM ready / 本地模型已就绪')
            completed(path)
        except (DownloadPaused,SetupCancelled):
            self.state='paused';self.message='Setup paused; Retry resumes the model download / 已暂停，可继续'
        except Exception as exc:
            self.state='failed';self.message=f'Setup failed; click Retry / 准备失败：{exc}'
