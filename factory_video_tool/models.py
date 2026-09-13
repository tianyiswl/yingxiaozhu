"""Portable artifacts; no UI, model runtime or platform imports."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
from datetime import datetime, timezone

def now():
    return datetime.now(timezone.utc).isoformat()

def text_hash(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()

def file_hash(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()

def frozen(value):
    return json.loads(json.dumps(value,ensure_ascii=False))

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)

@dataclass(frozen=True)
class SpeechArtifact:
    path: str
    duration: float
    provider_id: str
    text_hash: str
    timing_source: str = 'estimated_from_text'
