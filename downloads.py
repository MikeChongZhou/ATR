"""Resumable downloads: publish completed files atomically, retain partials on pause."""
import hashlib
import os
from pathlib import Path
import urllib.request
import urllib.error

class DownloadPaused(Exception):
    pass


def download_file(url, target, cancelled=lambda: False, progress=lambda p,m: None, sha256=None):
    target=Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    partial=target.with_name(target.name+'.partial')
    if target.is_file():
        if sha256:
            with target.open('rb') as f:
                if hashlib.file_digest(f,'sha256').hexdigest()!=sha256: target.unlink()
                else:return target
        else:return target
    offset=partial.stat().st_size if partial.exists() else 0
    if cancelled():raise DownloadPaused()
    headers={'User-Agent':'ATR-model-setup'}
    if offset: headers['Range']=f'bytes={offset}-'
    request=urllib.request.Request(url,headers=headers)
    try:
        response=urllib.request.urlopen(request,timeout=30)
    except urllib.error.HTTPError as exc:
        if exc.code==416 and offset:
            if sha256:
                with partial.open('rb') as f: complete=hashlib.file_digest(f,'sha256').hexdigest()==sha256
                if complete:
                    if cancelled():raise DownloadPaused()
                    os.replace(partial,target);progress(100,'Download complete');return target
            partial.unlink(); return download_file(url,target,cancelled,progress,sha256)
        raise
    with response:
        resumed=response.status==206 and response.headers.get('Content-Range','').startswith(f'bytes {offset}-')
        if response.status==206 and not resumed:raise RuntimeError('Invalid resume response')
        if not resumed:offset=0
        total=offset+int(response.headers.get('Content-Length','0'))
        received=offset
        with partial.open('ab' if resumed else 'wb') as output:
            while True:
                if cancelled():raise DownloadPaused()
                block=response.read(1024*1024)
                if not block:break
                output.write(block);received+=len(block)
                progress(min(99.9,received*100/total) if total else 0,f'{received/2**20:.1f} / {total/2**20:.1f} MiB')
            output.flush();os.fsync(output.fileno())
        if total and received!=total:raise RuntimeError('Download interrupted; retry to resume')
    if sha256:
        with partial.open('rb') as f:actual=hashlib.file_digest(f,'sha256').hexdigest()
        if actual!=sha256:
            partial.unlink();raise RuntimeError('Downloaded model checksum mismatch; retry')
    if cancelled():raise DownloadPaused()
    os.replace(partial,target)
    progress(100,'Download complete')
    return target
