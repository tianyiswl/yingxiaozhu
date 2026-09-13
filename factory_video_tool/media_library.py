"""Read-only material inspection with derived thumbnails in the app workspace."""
import hashlib
from pathlib import Path
from .core import Runner,scan_materials,executable,Cancelled

def inspect_folder(root,cache,cancel,progress):
    progress('正在检查素材，首次检查可能需要一点时间…')
    runner=Runner(cancel)
    valid,errors=scan_materials(root,runner)
    cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
    items=[]
    for i,(p,seconds) in enumerate(valid):
        runner.check();progress('正在整理素材 %s / %s'%(i+1,len(valid)))
        stat=p.stat();key=hashlib.sha256((str(p)+str(stat.st_mtime_ns)+str(stat.st_size)).encode()).hexdigest()
        thumb=cache/(key+'.jpg')
        if not thumb.exists():
            try:runner.run([executable('ffmpeg'),'-y','-v','error','-ss',str(min(.5,seconds/2)),'-i',p,'-frames:v','1','-vf','scale=240:136:force_original_aspect_ratio=decrease',thumb],timeout=60)
            except Cancelled:raise
            except Exception:pass
        items.append(dict(path=str(p),duration=seconds,thumbnail=str(thumb) if thumb.exists() else ''))
    return dict(root=str(Path(root).resolve()),items=items,errors=errors)
