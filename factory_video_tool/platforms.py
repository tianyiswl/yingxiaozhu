"""Only this module knows host TTS, font, and workspace conventions."""
import json
import os
import platform
import re
from pathlib import Path
from .core import executable

class PathResolver:
    def __init__(self,workspace=None):
        if workspace is not None:self.workspace=Path(workspace).resolve()
        else:
            from .storage import read_config,program_dir
            self.workspace=Path(read_config().get('data_dir',program_dir()/'data'))
    def prepare(self):
        self.workspace.mkdir(parents=True,exist_ok=True)
        return self.workspace

def resolve_font():
    from PySide6.QtGui import QFontDatabase
    available=set(QFontDatabase.families())
    candidates=('Microsoft YaHei','微软雅黑','Microsoft YaHei UI','微软雅黑 UI','SimHei','黑体','SimSun','宋体','DengXian','等线') if platform.system()=='Windows' else ('PingFang SC','Heiti SC','Songti SC')
    for name in candidates:
        if name in available:return name
    # Other installed CJK families are valid even if absent from our preferred list.
    from PySide6.QtGui import QFont,QRawFont
    for name in sorted(available):
        raw=QRawFont.fromFont(QFont(name))
        if raw.isValid() and all(raw.supportsCharacter(ord(c)) for c in '中文标题'):return name
    raise RuntimeError('未检测到可用中文字体，请在 Windows 设置中安装微软雅黑或宋体后重试')

class MacSystemTTSProvider:
    provider_id='macos_system_dev'
    def __init__(self,runner):self.runner=runner
    def list_voices(self):
        raw=self.runner.run(['say','-v','?'],timeout=30)
        return [m.group(1).strip() for line in raw.splitlines() if (m:=re.match(r'(.+?)\s+zh_\w+\s+#',line))]
    def synthesize(self,request):
        text,voice,rate,work=request
        (work/'speech.txt').write_text(text,encoding='utf-8')
        self.runner.run(['say','-v',voice,'-r',str(round(180*rate)),'-f',work/'speech.txt','-o',work/'speech.aiff'])
        self.runner.run([executable('ffmpeg'),'-y','-v','error','-i',work/'speech.aiff','-ar','44100','-ac','1','-c:a','pcm_s16le',work/'voice.wav'])
        return work/'voice.wav'

class WindowsSAPIProvider:
    provider_id='windows_sapi'
    def __init__(self,runner):self.runner=runner
    def _run(self,script,work):
        path=work/'sapi.ps1';path.write_text(script,encoding='utf-8-sig')
        return self.runner.run(['powershell.exe','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',path],cwd=work)
    def list_voices(self):
        # No persistent machine policy change; only this process executes a project script.
        script="$OutputEncoding = [Console]::OutputEncoding = [Text.UTF8Encoding]::new(); $v=New-Object -ComObject SAPI.SpVoice; @($v.GetVoices() | ForEach-Object {$_.Id}) | ConvertTo-Json -Compress"
        return self._decode(self.runner.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],timeout=30))
    @staticmethod
    def _decode(raw):
        voices=json.loads(raw.lstrip('\ufeff'))
        return [voices] if isinstance(voices,str) else voices
    def synthesize(self,request):
        text,voice,rate,work=request
        (work/'sapi-request.json').write_text(json.dumps(dict(text=text,voice=voice,rate=round((rate-1)*10)),ensure_ascii=False),encoding='utf-8-sig')
        self._run('''$ErrorActionPreference='Stop'
$r=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'sapi-request.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$v=New-Object -ComObject SAPI.SpVoice
$found=$false
foreach($t in $v.GetVoices()){if($t.Id -eq $r.voice){$v.Voice=$t;$found=$true;break}}
if(-not $found){throw 'Selected SAPI voice is unavailable'}
$v.Rate=$r.rate
$v.Volume=100
$s=New-Object -ComObject SAPI.SpFileStream
$s.Format.Type=22
$s.Open((Join-Path $PSScriptRoot 'sapi.wav'),3,$false)
try {$v.AudioOutputStream=$s; [void]$v.Speak($r.text,16)} finally {$s.Close()}
''',work)
        self.runner.run([executable('ffmpeg'),'-y','-v','error','-i',work/'sapi.wav','-ar','44100','-ac','1','-c:a','pcm_s16le',work/'voice.wav'])
        return work/'voice.wav'

def tts_provider(runner):
    system=platform.system()
    if system=='Darwin':return MacSystemTTSProvider(runner)
    if system=='Windows':return WindowsSAPIProvider(runner)
    raise RuntimeError('本版本仅支持 macOS 开发和 Windows 生产环境')
