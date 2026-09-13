from ..generation import render
from ..clip_avoidance import recent_clip_history

class FootageRenderer:
    provider_id='footage_ffmpeg_v1'
    def __init__(self,repository=None):self.repository=repository
    def render(self,request,speech,work,cancel,progress):
        history=recent_clip_history(self.repository) if request.get('avoid_recent_clips') and self.repository is not None else None
        return render(request,speech,work,cancel,progress,clip_history=history)
