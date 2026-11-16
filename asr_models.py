"""Optional local ASR engines and their exact, tested model files."""
from pathlib import Path
import shutil
import tarfile
import tempfile

SPECS = {
    "zipformer-small-zh-en": dict(label="Zipformer small Chinese + English (lightweight)", engine="zipformer",
        local_dir="sherpa-onnx-streaming-zipformer-small-bilingual-zh-en-2023-02-16",
        repo_id="github:k2-fsa/sherpa-onnx", files=("tokens.txt", "encoder-epoch-99-avg-1.int8.onnx", "decoder-epoch-99-avg-1.onnx", "joiner-epoch-99-avg-1.int8.onnx")),
    "sensevoice-small-int8": dict(label="SenseVoice Small INT8 (Chinese / English / Cantonese / Japanese / Korean)", engine="sensevoice",
        local_dir="sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
        repo_id="csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17", files=("tokens.txt", "model.int8.onnx")),
    "paraformer-zh-en-int8": dict(label="Streaming Paraformer Chinese + English INT8", engine="paraformer",
        local_dir="sherpa-onnx-streaming-paraformer-bilingual-zh-en",
        repo_id="csukuangfj/sherpa-onnx-streaming-paraformer-bilingual-zh-en", files=("tokens.txt", "encoder.int8.onnx", "decoder.int8.onnx")),
}


def presets(language):
    return [dict(id=key, label=s['label'], engine=s['engine'], repo_id=s['repo_id'],
                 model_name=key, local_dir=s['local_dir'], language=language) for key,s in SPECS.items()]


def spec_for(value):
    return SPECS.get(str(value))


def infer_engine(config):
    spec = spec_for(config.get('model_preset')) or spec_for(config.get('model_name'))
    return spec['engine'] if spec else config.get('engine', 'whisper')


def usable(path):
    path = Path(path)
    if (path/'config.json').is_file() and (path/'model.bin').is_file():
        return True
    return any(all((path/f).is_file() and (path/f).stat().st_size for f in s['files']) for s in SPECS.values())


def download(preset_id, target, progress=lambda p,m: None):
    spec = SPECS[preset_id]
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    if spec['engine'] == 'zipformer':
        from downloads import download_file
        url = 'https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/'+spec['local_dir']+'.tar.bz2'
        archive = target/'.model.tar.bz2'
        download_file(url, archive, progress=lambda p,m: progress(p*.9, m))
        # Extract only known files; never trust archive paths or links.
        with tempfile.TemporaryDirectory(dir=target) as scratch:
            remaining = set(spec['files'])
            with tarfile.open(archive, 'r|bz2') as bundle:
                for item in bundle:
                    expected = spec['local_dir']+'/'+Path(item.name).name
                    if item.isfile() and item.name == expected and Path(item.name).name in remaining:
                        name = Path(item.name).name
                        with bundle.extractfile(item) as src, (Path(scratch)/name).open('wb') as dst:
                            shutil.copyfileobj(src,dst)
                        remaining.remove(name)
                        if not remaining: break
            if remaining: raise RuntimeError(f'Model archive is incomplete: {remaining}')
            for name in spec['files']:
                (Path(scratch)/name).replace(target/name)
        archive.unlink(missing_ok=True)
    else:
        from huggingface_hub import hf_hub_download, HfApi
        revision = HfApi().model_info(spec['repo_id']).sha
        for i,name in enumerate(spec['files']):
            progress(100*i/len(spec['files']), f'Downloading {name}')
            hf_hub_download(spec['repo_id'], name, revision=revision, local_dir=str(target))
    if not usable(target): raise RuntimeError('Downloaded model files are incomplete')
    progress(100, 'Model ready')


class SherpaEngine:
    def __init__(self, config):
        import sherpa_onnx as so
        self.engine = infer_engine(config)
        path = Path(config['model_path'])
        if not usable(path): raise RuntimeError('Model not downloaded. Open System Settings and download the selected model.')
        common = dict(tokens=str(path/'tokens.txt'), num_threads=max(1,int(config.get('cpu_threads',2))), sample_rate=16000, provider='cpu')
        if self.engine == 'sensevoice':
            self.model = so.OfflineRecognizer.from_sense_voice(**common, model=str(path/'model.int8.onnx'), language='auto', use_itn=True)
        elif self.engine == 'paraformer':
            self.model = so.OnlineRecognizer.from_paraformer(**common, encoder=str(path/'encoder.int8.onnx'),decoder=str(path/'decoder.int8.onnx'))
        else:
            self.model = so.OnlineRecognizer.from_transducer(**common,
                encoder=str(path/'encoder-epoch-99-avg-1.int8.onnx'),decoder=str(path/'decoder-epoch-99-avg-1.onnx'),joiner=str(path/'joiner-epoch-99-avg-1.int8.onnx'))

    def transcribe(self, audio, sample_rate=16000):
        import numpy as np
        import re
        stream = self.model.create_stream()
        if self.engine == 'sensevoice':
            stream.accept_waveform(sample_rate, audio)
            self.model.decode_stream(stream)
            return re.sub(r'<\|.*?\|>', '', stream.result.text).strip()
        # Each ATR job is finalized independently so source streams never mix.
        for start in range(0,len(audio),max(1,int(sample_rate*.2))):
            stream.accept_waveform(sample_rate,audio[start:start+int(sample_rate*.2)])
            while self.model.is_ready(stream): self.model.decode_stream(stream)
        stream.accept_waveform(sample_rate,np.zeros(int(sample_rate*.5),np.float32))
        stream.input_finished()
        while self.model.is_ready(stream): self.model.decode_stream(stream)
        return self.model.get_result(stream).strip()
