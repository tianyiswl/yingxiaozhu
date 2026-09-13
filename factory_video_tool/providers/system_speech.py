from ..core import Runner,probe,duration,synthesize_with_retry
from ..platforms import tts_provider
from ..models import SpeechArtifact,text_hash

class SystemSpeech:
    provider_id='system_speech_v1'
    def synthesize(self,request,work,cancel,progress):
        progress('系统配音')
        provider=tts_provider(Runner(cancel))
        path=synthesize_with_retry(provider,(request['content']['voice_text'],request['voice'],request['rate'],work))
        seconds=duration(probe(path,Runner(cancel)))
        return SpeechArtifact(str(path),seconds,provider.provider_id,text_hash(request['content']['voice_text']))
