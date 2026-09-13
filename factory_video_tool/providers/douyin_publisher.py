"""Official Douyin OpenAPI transport. Requires the user's approved application scopes.

Optional OpenAPI adapter: requires separately approved application permissions.
No browser session scraping, retries or automatic account substitution.
"""
import http.client
import json
import ssl
import uuid
from pathlib import Path
from urllib.parse import urlsplit,urlencode
from ..core import Runner

class APIError(RuntimeError):
    def __init__(self,code):
        self.code=str(code)
        super().__init__('抖音接口返回错误码 '+self.code+'；请核对授权、权限或请求参数')

class HTTPTransport:
    def __init__(self,base_url='https://open.douyin.com'):
        u=urlsplit(base_url)
        if not ((u.scheme=='https' and u.hostname=='open.douyin.com') or (u.scheme=='http' and u.hostname in ('127.0.0.1','localhost'))):
            raise ValueError('发布地址必须为抖音官方服务；本地回环仅供协议测试')
        self.base=u
    def post(self,path,token,cancel,payload=None,video=None):
        Runner(cancel).check()
        conn=(http.client.HTTPSConnection(self.base.hostname,self.base.port,timeout=15,context=ssl.create_default_context()) if self.base.scheme=='https' else http.client.HTTPConnection(self.base.hostname,self.base.port,timeout=15))
        headers={'access-token':token}
        try:
            if video:
                p=Path(video)
                # The first release uses a bounded upload, not an unimplemented multipart fallback.
                if p.stat().st_size>50*1024*1024:raise ValueError('首版接口支持50MB以内成片，请降低码率后再上传')
                boundary='P009'+uuid.uuid4().hex
                prefix=('--'+boundary+'\r\nContent-Disposition: form-data; name="video"; filename="video.mp4"\r\nContent-Type: video/mp4\r\n\r\n').encode()
                suffix=('\r\n--'+boundary+'--\r\n').encode()
                headers.update({'Content-Type':'multipart/form-data; boundary='+boundary,'Content-Length':str(len(prefix)+p.stat().st_size+len(suffix))})
                conn.putrequest('POST',path)
                for k,v in headers.items():conn.putheader(k,v)
                conn.endheaders();conn.send(prefix)
                with p.open('rb') as f:
                    for chunk in iter(lambda:f.read(256*1024),b''):
                        Runner(cancel).check();conn.send(chunk)
                conn.send(suffix)
            else:
                body=json.dumps(payload or {},ensure_ascii=False).encode('utf-8')
                headers['Content-Type']='application/json'
                conn.request('POST',path,body,headers)
            response=conn.getresponse();raw=response.read(4*1024*1024)
            if response.status!=200:raise RuntimeError('抖音服务HTTP状态 '+str(response.status))
            Runner(cancel).check()
            try:data=json.loads(raw)
            except (ValueError,UnicodeError):raise RuntimeError('抖音响应无法解析，提交结果需要查询') from None
            part=data.get('data')
            if not isinstance(part,dict) or 'error_code' not in part:raise RuntimeError('抖音响应缺少明确结果')
            for record in (part,data.get('extra',{})):
                if str(record.get('error_code',0))!='0':raise APIError(record['error_code'])
            return part
        except (OSError,http.client.HTTPException):
            # Never include URL, headers or token in errors/logs.
            raise RuntimeError('网络连接中断或超时；若已进入提交，请查询原记录') from None
        finally:conn.close()

class DouyinPublisher:
    provider_id='douyin_openapi_v1'
    def __init__(self,token_getter,transport=None):
        self.token_getter=token_getter;self.transport=transport or HTTPTransport()
    def _post(self,path,cancel,account=None,payload=None,video=None):
        token=self.token_getter()
        if not token:raise ValueError('尚未配置抖音开放平台授权，请先连接固定账号')
        if account:path+='?'+urlencode({'open_id':account['platform_user_id']})
        return self.transport.post(path,token,cancel,payload,video)
    def identity(self,cancel):
        result=self._post('/oauth/userinfo/',cancel)
        if not result.get('open_id') or not result.get('nickname'):raise ValueError('无法核对抖音账号身份')
        return {'platform':'douyin','platform_user_id':result['open_id'],'display_name':result['nickname']}
    def upload(self,path,account,cancel):
        result=self._post('/api/douyin/v1/video/upload_video/',cancel,account,video=path)
        ident=result.get('video',{}).get('video_id')
        if not ident:raise RuntimeError('上传未返回视频文件ID')
        return ident
    def submit(self,video_id,text,account,cancel):
        if not text.strip() or len(text)>1000:raise ValueError('发布文字必须为1—1000字，请先编辑')
        result=self._post('/api/douyin/v1/video/create_video/',cancel,account,{'video_id':video_id,'text':text,'private_status':0})
        if not result.get('item_id'):raise RuntimeError('提交未返回作品ID，不能确认结果')
        return {'item_id':result['item_id'],'video_id':result.get('video_id'),'visibility':'UNKNOWN','review':'UNKNOWN'}
    def query(self,item_id,account,cancel):
        result=self._post('/api/douyin/v1/video/video_basic_info/',cancel,account,{'item_ids':[item_id]})
        rows=[r for r in result.get('list',[]) if r.get('item_id')==item_id]
        if len(rows)!=1:raise ValueError('未查到该账号的唯一作品，保持原状态')
        r=rows[0];status=r.get('video_status')
        # These fields were deprecated by an official bulletin. Missing is UNKNOWN, never success.
        visibility={5:'PUBLIC',2:'NOT_PUBLIC',6:'NOT_PUBLIC',7:'NOT_PUBLIC'}.get(status,'UNKNOWN')
        review={4:'PENDING',2:'REJECTED',5:'APPROVED',6:'APPROVED',7:'APPROVED'}.get(status,'UNKNOWN')
        video_id=str(r.get('video_id',''))
        url='https://www.douyin.com/video/'+video_id if video_id.isdigit() else None
        return {'item_id':item_id,'video_id':video_id,'title':r.get('title'),'create_time':r.get('create_time'),
                'visibility':visibility,'review':review,'platform_status':status,'candidate_url':url,'source':'douyin_video_basic_info'}
