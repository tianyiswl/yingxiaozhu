import json
import math
import os
import platform
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from .core import Runner, Cancelled, executable, probe, duration, scan_materials, choose_clips, subtitle_cues, synthesize_with_retry
from .platforms import tts_provider

def draw_subtitles(cues,work,width,font,font_size=None,font_color='#FFFFFF'):
    if not font:
        from .platforms import resolve_font
        font=resolve_font()
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QImage, QPainter, QFont, QPainterPath, QColor
    def timestamp(t):
        ms=round(t*1000);return '%02d:%02d:%02d,%03d'%(ms//3600000,ms//60000%60,ms//1000%60,ms%1000)
    color=QColor(font_color)
    if not color.isValid():raise ValueError('字幕颜色无效')
    size=round(width*.045) if font_size is None else round(float(font_size)*width/720)
    if not 16*width/720<=size<=72*width/720:raise ValueError('字幕字号需在16到72之间（按720p计）')
    from PySide6.QtGui import QFontMetrics
    f=QFont(font);f.setPixelSize(size);metrics=QFontMetrics(f)
    wrapped=[]
    for _,_,text in cues:
        lines=['']
        for char in text:
            if metrics.horizontalAdvance(lines[-1]+char)>width*.9 and lines[-1]:lines.append('')
            lines[-1]+=char
        wrapped.append(lines)
    height=max(round(width*.3),max(map(len,wrapped))*metrics.height()+round(width*.06))
    srt=[];concat=['ffconcat version 1.0']
    for i,(start,end,text) in enumerate(cues):
        srt.append('%d\n%s --> %s\n%s\n'%(i+1,timestamp(start),timestamp(end),text))
        image=QImage(width,height,QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setFont(f)
        path=QPainterPath();lines=wrapped[i]
        top=(height-len(lines)*metrics.height())/2
        for line_no,line in enumerate(lines):
            x=(width-metrics.horizontalAdvance(line))/2
            path.addText(x,top+metrics.ascent()+line_no*metrics.height(),f,line)
        from PySide6.QtGui import QPen
        painter.strokePath(path,QPen(QColor('black'),max(2,width/180)))
        painter.fillPath(path,color);painter.end()
        name='subtitle-%04d.png'%i
        if not image.save(str(work/name)):raise RuntimeError('字幕图片保存失败')
        concat.extend(["file '%s'"%name,'duration %.8f'%(end-start)])
    concat.append("file '%s'"%name)
    (work/'subtitles.ffconcat').write_text('\n'.join(concat)+'\n',encoding='utf-8')
    (work/'subtitles.srt').write_text('\n'.join(srt),encoding='utf-8')


def generate(request,cancel,progress):
    """Compatibility entry point; new workflow injects speech and renderer separately."""
    from .providers.system_speech import SystemSpeech
    workspace=Path(request['workspace']).resolve();workspace.mkdir(parents=True,exist_ok=True)
    work=workspace/('run-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:8]);work.mkdir()
    try:
        speech=SystemSpeech().synthesize(request,work,cancel,progress)
        return render(request,speech,work,cancel,progress)
    except Exception as exc:
        (work/'failure.json').write_text(json.dumps({'status':'CANCELLED' if isinstance(exc,Cancelled) else 'FAILED','error':str(exc)},ensure_ascii=False))
        raise


def render(request,speech,work,cancel,progress,clip_history=None):
    """Consume an existing speech artifact. Never invokes a TTS provider."""
    from .models import file_hash,text_hash
    work=Path(work).resolve();work.mkdir(parents=True,exist_ok=True)
    manifest=dict(request,status='PENDING',platform=platform.system(),work_dir=str(work))
    def save(): (work/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    def log(s):
        with (work/'process.log').open('a',encoding='utf-8') as f:f.write(s+'\n')
    runner=Runner(cancel,log)
    def stage(s):manifest['status']=s;save();progress(s)
    save()
    try:
        stage('检查产品素材')
        snapshot=request.get('material_snapshot')
        if snapshot:
            materials=[];warnings=[]
            for entry in snapshot:
                runner.check();path=Path(entry['path'])
                if not path.is_file():raise ValueError('素材原文件失效：'+str(path)+'；请连接硬盘或在素材库重新定位后重新创建任务')
                if file_hash(path)!=entry['sha256']:raise ValueError('素材发生变化，请重新创建任务：'+str(path))
                info=probe(path,runner)
                if not any(s['codec_type']=='video' for s in info['streams']):raise ValueError('素材没有视频流')
                runner.run([executable('ffmpeg'),'-v','error','-xerror','-i',path,'-map','0:v:0','-an','-f','null','-'])
                materials.append((path,duration(info)))
        else:materials,warnings=scan_materials(request['material_root'],runner)
        bgm=request.get('bgm')
        if bgm:
            info=probe(bgm,runner)
            if not any(s['codec_type']=='audio' for s in info['streams']):raise ValueError('BGM 没有音频流')
            duration(info)
            runner.run([executable('ffmpeg'),'-v','error','-xerror','-i',bgm,'-map','0:a:0','-f','null','-'])
        if speech.text_hash!=text_hash(request['content']['voice_text']):raise ValueError('配音与当前口播不匹配')
        voice=Path(speech.path).resolve()
        if voice!=work/'voice.wav':
            runner.run([executable('ffmpeg'),'-y','-v','error','-i',voice,'-ar','44100','-ac','1','-c:a','pcm_s16le',work/'voice.wav'])
            voice=work/'voice.wav'
        manifest['tts_provider']=speech.provider_id
        manifest['subtitle_timing']=speech.timing_source
        voice_duration=duration(probe(voice,runner));target=voice_duration+.3
        if request.get('avoid_recent_clips') and clip_history is not None:
            from .clip_avoidance import choose_diverse_clips
            hashes={str(p):file_hash(p) for p,_ in materials}
            clips,avoidance=choose_diverse_clips(materials,target,hashes,clip_history)
            manifest['clip_avoidance']=avoidance
            warnings.extend(avoidance['warnings'])
        else:
            clips=choose_clips(materials,target)
            if len(clips)>len(materials):warnings.append('可用素材较少，本条视频中已复用所选目录内的素材')
        manifest.update(voice_duration=voice_duration,target_duration=target,clips=[(str(p),s,d) for p,s,d in clips],warnings=warnings)
        progress('\n'.join(warnings))
        width=request.get('width',1080);height=width*16//9
        stage('生成字幕')
        cues=subtitle_cues(request['content']['voice_text'],voice_duration,request.get('subtitle_max_chars',14))
        draw_subtitles(cues,work,width,request['font'],request.get('font_size'),request.get('font_color','#FFFFFF'))
        base=[executable('ffmpeg'),'-y','-v','error','-xerror']
        # The concat demuxer can stop advancing a sparse PNG stream midway
        # through an overlay on some source videos. Turn it into a normal
        # alpha video first so every subtitle cue has a stable frame timeline.
        runner.run(base+['-f','concat','-safe','0','-i','subtitles.ffconcat','-t',str(voice_duration),'-vf','fps=30','-c:v','qtrle','-pix_fmt','argb','subtitles.mov'],cwd=work)
        concat=['ffconcat version 1.0']
        for i,(path,start,length) in enumerate(clips):
            stage('处理镜头 %d/%d'%(i+1,len(clips)))
            name='clip-%04d.mp4'%i
            runner.run(base+['-ss',str(start),'-i',path,'-t',str(length),'-map','0:v:0','-an','-vf',f'scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height},setsar=1,fps=30','-c:v','libx264','-preset','ultrafast','-crf','23','-pix_fmt','yuv420p',work/name])
            concat.append("file '%s'"%name)
        (work/'clips.ffconcat').write_text('\n'.join(concat)+'\n',encoding='utf-8')
        stage('合成竖屏视频')
        runner.run(base+['-f','concat','-safe','0','-i','clips.ffconcat','-c','copy','joined.mp4'],cwd=work)
        args=base+['-i','joined.mp4','-i','voice.wav','-i','subtitles.mov']
        vf=f'[0:v][2:v]overlay=0:H-h-{round(height*.12)}:eof_action=repeat:enable=lt(t\\,{voice_duration})[v];'
        if bgm:
            args+=['-stream_loop','-1','-i',str(Path(bgm).resolve())]
            af=f'[1:a]apad,atrim=duration={target}[speech];[3:a]volume={request.get("bgm_volume",.15)},atrim=duration={target},afade=t=in:d=0.5,afade=t=out:st={max(0,target-.5)}:d=0.5[music];[speech][music]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95:latency=1[a]'
        else:af=f'[1:a]apad,atrim=duration={target},alimiter=limit=0.95:latency=1[a]'
        args+=['-filter_complex',vf+af,'-map','[v]','-map','[a]','-t',str(target),'-r','30','-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart','result.partial.mp4']
        runner.run(args,cwd=work)
        stage('验证成片')
        info=probe(work/'result.partial.mp4',runner)
        actual=duration(info)
        if abs(actual-target)>.15:raise RuntimeError('成片时长偏差超过 0.15 秒')
        video=next(s for s in info['streams'] if s['codec_type']=='video')
        audio=next(s for s in info['streams'] if s['codec_type']=='audio')
        if (video['width'],video['height'],video['codec_name'],audio['codec_name'])!=(width,height,'h264','aac'):raise RuntimeError('成片编码或尺寸不符合要求')
        runner.run(base[:1]+['-v','error','-xerror','-i',work/'result.partial.mp4','-f','null','-'])
        runner.check();output=work/'result.mp4';(work/'result.partial.mp4').replace(output)
        manifest.update(status='SUCCESS',output=str(output),duration=actual);save()
        progress('生成完成：'+str(output))
        return str(output)
    except Exception as e:
        manifest.update(status='CANCELLED' if isinstance(e,Cancelled) else 'FAILED',error=str(e));save();log(str(e))
        raise type(e)(str(e)+'\n过程记录：'+str(work)) from e


def export_video(source,destination,cancel,progress):
    source=Path(source).resolve();destination=Path(destination).resolve()
    if source==destination:return str(source)
    if destination.exists():raise ValueError('目标文件已存在，请使用新文件名')
    temp=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.partial')
    try:
        with source.open('rb') as src,temp.open('xb') as dst:
            while True:
                if cancel.is_set():raise Cancelled('导出已取消，原成片保留')
                chunk=src.read(1024*1024)
                if not chunk:break
                dst.write(chunk)
        if cancel.is_set():raise Cancelled('导出已取消，原成片保留')
        # Atomic, exclusive final publication on the same filesystem; never overwrite.
        os.link(temp,destination)
        return str(destination)
    finally:temp.unlink(missing_ok=True)
