"""Visible browser publication, adapted from user-owned 一键发 field routines.

No credential export, no request signing, and no automatic verification solving.
Only an explicit commit callback can authorize the single final Publish click.
"""
import re
import time
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit
from ..core import Runner, Cancelled
from ..models import file_hash
from .oneclick_form import OneClickForm

EDITOR_URL='https://creator.douyin.com/creator-micro/content/post/video?enter_from=publish_page'
MANAGE_URL='https://creator.douyin.com/creator-micro/content/manage'
SHANGHAI=ZoneInfo('Asia/Shanghai')


def validate_content(content):
    title=str(content.get('title','')).strip()
    body=str(content.get('body') or title).strip()
    if not title or not body:raise ValueError('标题和发布正文不能为空')
    if len(title)>30:raise ValueError('当前抖音适配器标题最多30字，请在本地核对修改')
    if '#' in body or re.search(r'(?:^|\s)@[^\s@#]+',body):
        raise ValueError('正文不能包含手写话题或提及；话题请放在独立标签字段')
    raw=content.get('tags','')
    values=raw if isinstance(raw,list) else re.split(r'[\s,，]+',str(raw).strip())
    topics=[str(x).strip().lstrip('#') for x in values if str(x).strip().lstrip('#')]
    if len(topics)>5 or len(set(topics))!=len(topics):raise ValueError('请使用最多5个不重复的抖音话题')
    if any(re.search(r'[\s#@]',x) for x in topics):raise ValueError('标签字段格式无法识别')
    if len(body)+sum(len(x)+2 for x in topics)>1000:raise ValueError('正文与话题合计最多1000字，不会自动截断')
    return {'title':title,'body':body,'topics':topics}


class DouyinBrowserPublisher:
    def __init__(self, session):self.session=session;self.pending=None
    def _check_account(self, session, account, cancel, progress):
        session._ensure_browser()
        actual=session._login(cancel,progress,20,False)
        if actual['platform_user_id']!=account['platform_user_id']:raise ValueError('当前抖音账号与本次发布账号不一致')
        return actual
    @staticmethod
    def _current_account(account):
        if not account.get('verified') or not account.get('platform_user_id'):
            raise ValueError('抖音登录状态未确认，请重新点击登录')
        return account
    @staticmethod
    def _unique(locator, label):
        items=OneClickForm._visible_enabled_items(locator)
        if len(items)!=1:raise ValueError(label+'无法唯一确认，已停止操作')
        return items[0]
    def _upload_input(self,page,cancel):
        """Wait for either current Douyin upload control used by 一键发."""
        selectors=(
            "input[type=file][accept*='video']",
            "div[class^='upload-card'] input[type=file][accept*='video']",
            "div.progress-div [class^='upload-btn-input']",
            "input[type=file]",
        )
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            Runner(cancel).check()
            for selector in selectors:
                candidates=page.locator(selector)
                if candidates.count()==1:
                    return candidates
            if page.get_by_text('手机号登录',exact=True).count() or page.get_by_text('扫码登录',exact=True).count():
                raise ValueError('抖音发布页要求重新登录，请点击登录抖音完成扫码后再试')
            page.wait_for_timeout(500)
        try:title=page.title(timeout=2000)
        except Exception:title='无法读取页面标题'
        raise ValueError('未进入抖音视频发布页（当前地址：%s；页面标题：%s）'%(page.url,title))
    def _open_editor(self,page,cancel):
        page.goto(EDITOR_URL,wait_until='commit',timeout=30000)
        return self._upload_input(page,cancel)
    def inspect_editor(self, cancel, progress):
        """Read-only live capability probe. No file selection or field writes."""
        def action(session,cancel,progress):
            account=session.repository.setting('fixed_account',{})
            self._check_account(session,account,cancel,progress)
            page=session._page
            upload=self._open_editor(page,cancel)
            return {'account_verified':True,'editor_opened':True,'upload_inputs':upload.count(),'files_selected':0,'published':False}
        return self.session.execute(action,cancel,progress)
    def prepare(self,path,content,account,cancel,progress,scheduled_for=None):
        def action(session,cancel,progress):
            if self.pending:raise ValueError('已有待确认页面，请先确认或取消原准备记录')
            self._check_account(session,account,cancel,progress)
            page=session._page;form=OneClickForm(content,cancel)
            try:
                upload=self._open_editor(page,cancel);page.bring_to_front()
                if upload.count()!=1:raise ValueError('上传控件无法唯一确认')
                if page.get_by_text(re.compile('上次未发布的视频|继续编辑')).count():
                    raise ValueError('抖音有上次未完成的编辑，请先在官方页面处理，未覆盖旧内容')
                Runner(cancel).check();progress('正在上传冻结成片；尚未点击发布')
                upload.set_input_files(path)
                deadline=time.monotonic()+360
                while time.monotonic()<deadline:
                    Runner(cancel).check()
                    if OneClickForm._visible_items(page.locator('[class^="long-card"] div').filter(has_text=re.compile('^重新上传$'))):break
                    if OneClickForm._visible_items(page.get_by_text('上传失败',exact=True)):raise ValueError('抖音提示上传失败，未自动重复上传')
                    self._verification(page,cancel,progress,deadline)
                    page.wait_for_timeout(300)
                else:raise ValueError('上传等待超时；未点击发布')
                progress('核对抖音标题、正文和官方话题');readback=form.fill(page)
                scheduled_at=datetime.fromisoformat(scheduled_for) if scheduled_for else None
                if scheduled_at:
                    if (scheduled_at-datetime.now(SHANGHAI)).total_seconds()<7800:
                        raise ValueError('定时余量不足，转本机等待')
                    self._set_schedule(page,scheduled_at)
                else:
                    switch=self._unique(page.locator('label').filter(has_text=re.compile(r'^\s*立即发布\s*$')),'立即发布选项')
                    switch.click(force=True)
                self._check_defaults(page,scheduled_at)
                token=uuid.uuid4().hex
                self.pending={'token':token,'page':page,'form':form,'path':path,'sha256':file_hash(path),'account_id':account['platform_user_id'],'scheduled_for':scheduled_at.isoformat() if scheduled_at else None}
                return {'token':token,'title':content['title'],'body':content['body'],'topics':content['topics'],
                        'preflight':readback,'editor_url':EDITOR_URL,'scheduled_for':scheduled_at.isoformat() if scheduled_at else None,'submitted':False}
            except Exception:
                # Leave the sole publisher page visible for the user to inspect.
                raise
        return self.session.execute(action,cancel,progress)
    def _schedule_input(self,page):
        # 新版页面的日期输入框没有 placeholder；限定在“发布时间”所在
        # 发布设置块内，避免误选标题、付费等其他 semi-input。
        headings=OneClickForm._visible_items(page.get_by_text('发布时间',exact=True))
        if len(headings)==1:
            block=headings[0].locator("xpath=ancestor::div[contains(@class,'content-')][1]")
            items=OneClickForm._visible_enabled_items(block.locator('input.semi-input'))
            if len(items)==1:return items[0]
            if len(items)>1:raise ValueError('抖音定时发布时间输入框无法唯一确认')
        for selector in (".semi-input[placeholder='日期和时间']", "input[placeholder*='发布时间']", "input[placeholder*='选择时间']", "input[placeholder*='选择日期']"):
            items=OneClickForm._visible_enabled_items(page.locator(selector))
            if len(items)==1:return items[0]
            if len(items)>1:raise ValueError('抖音定时输入框无法唯一确认')
        raise ValueError('未找到抖音定时发布时间输入框，未点击发布')
    def _set_schedule(self,page,scheduled_for):
        # 一键发's live selector targets the radio container itself. Fall back
        # to its label for pages with a simplified DOM.
        switch=None
        for locator in (page.locator("[class^='radio']:has-text('定时发布')"),page.locator('label').filter(has_text=re.compile(r'^\s*定时发布\s*$'))):
            items=OneClickForm._visible_enabled_items(locator)
            if len(items)==1:
                switch=items[0];break
            if len(items)>1:raise ValueError('抖音定时发布开关无法唯一确认')
        if switch is None:raise ValueError('未找到抖音定时发布开关，未点击发布')
        for _ in range(3):
            if (switch.get_attribute('data-checked') or '').lower()=='true':break
            switch.click(force=True,timeout=5000);page.wait_for_timeout(1000)
        if (switch.get_attribute('data-checked') or '').lower()!='true':
            raise ValueError('抖音定时发布开关点击后未保持选中')
        field=None
        for _ in range(30):
            try:
                field=self._schedule_input(page);break
            except ValueError as exc:
                if '未找到' not in str(exc):raise
                page.wait_for_timeout(500)
        if field is None:raise ValueError('抖音开启定时发布后未找到可用日期输入框，未点击发布')
        expected=scheduled_for.strftime('%Y-%m-%d %H:%M')
        field.click(force=True,timeout=5000);field.fill(expected);page.keyboard.press('Enter');page.wait_for_timeout(800)
        try:
            field.evaluate("""node => {node.dispatchEvent(new Event('input',{bubbles:true}));node.dispatchEvent(new Event('change',{bubbles:true}));node.blur();}""")
        except Exception:page.keyboard.press('Escape')
        page.wait_for_timeout(800)
        actual=field.input_value().strip()
        if expected not in actual:raise ValueError('抖音定时发布时间回读不一致，未点击发布')
    def _check_defaults(self,page,scheduled_for=None):
        # Retained cross-post defaults must not silently widen this request.
        checked=page.locator('input:checked, [role="switch"][aria-checked="true"], [role="radio"][aria-checked="true"]')
        schedule=False
        for index in range(checked.count()):
            label=checked.nth(index).locator('xpath=..').inner_text()
            if '定时' in label:schedule=True
            if '头条' in label:raise ValueError('页面保留了同步头条，请在官方页面关闭后重新预检')
        if scheduled_for is None and schedule:raise ValueError('页面保留了定时，请在官方页面关闭后重新预检')
        if scheduled_for is not None and not schedule:raise ValueError('抖音定时开关状态无法确认，未点击发布')
    def _verification(self,page,cancel,progress,deadline):
        # 一键发 uses this panel and exact marker set. P009 keeps entry on the
        # official page; codes and QR bytes never enter our own UI/database.
        panels=OneClickForm._visible_items(page.locator('.second-verify-panel'))
        markers=any(OneClickForm._visible_items(page.get_by_text(x,exact=True)) for x in ('接收短信验证码','获取验证码','使用原设备扫码'))
        if not panels and not markers:return False
        progress('等待抖音安全验证：请在当前官方窗口完成验证码或扫码；程序不会再次点击发布')
        page.bring_to_front()
        while time.monotonic()<deadline:
            Runner(cancel).check()
            visible=OneClickForm._visible_items(page.locator('.second-verify-panel'))
            markers=any(OneClickForm._visible_items(page.get_by_text(x,exact=True)) for x in ('接收短信验证码','获取验证码','使用原设备扫码'))
            if not visible and not markers:return True
            page.wait_for_timeout(250)
        raise RuntimeError('等待用户验证超时，请核对平台结果，不能直接重发')
    def commit(self,receipt,account,cancel,progress,before_click):
        def action(session,cancel,progress):
            current=self.pending
            if not current or current['token']!=receipt.get('token') or current['page'].is_closed():
                raise ValueError('预检页面已失效，请取消原准备记录后重新预检')
            self.pending=None
            self._current_account(account)
            if current['account_id']!=account['platform_user_id']:raise ValueError('页面账号不一致')
            if file_hash(current['path'])!=current['sha256']:raise ValueError('上传源文件已改变')
            page=current['page'];page.bring_to_front();form=current['form'];form.cancel=cancel
            scheduled=current.get('scheduled_for')
            if scheduled and (datetime.fromisoformat(scheduled)-datetime.now(SHANGHAI)).total_seconds()<7800:
                self.pending=None
                raise ValueError('定时余量不足，转本机等待')
            form.verify_prepublish_form(page,require_covers=False);self._check_defaults(page,scheduled)
            button=self._unique(page.get_by_role('button',name='发布',exact=True),'发布按钮')
            if button.get_attribute('aria-disabled')=='true' or 'disabled' in (button.get_attribute('class') or '').lower():
                raise ValueError('抖音发布按钮当前不可用')
            Runner(cancel).check();before_click()
            try:
                button.click(timeout=10000)
                deadline=time.monotonic()+600
                while time.monotonic()<deadline:
                    Runner(cancel).check()
                    if urlsplit(page.url).path.startswith('/creator-micro/content/manage'):
                        # 抖音已离开发布页并进入官方作品管理页，表示平台已接收本次
                        # 定时提交。新管理页不会同步提供作品 ID，因此不能把缺少 ID
                        # 误写为失败或要求重发。
                        result={
                            'accepted':True, 'visibility':'SCHEDULED' if scheduled else 'UNKNOWN', 'review':'UNKNOWN',
                            'reason':'已进入抖音作品管理页，平台已接收提交',
                            'verified_by':'content_manage_navigation',
                            'scheduled_for':scheduled,
                        }
                        self.pending=None
                        # 成功后关闭应用专用浏览器；持久化浏览器配置仍会保留，下一次
                        # 发布前由后台重新核对登录状态。
                        dispose=getattr(session,'_dispose',None)
                        if callable(dispose):dispose()
                        return dict(result,submitted=True,manage_navigation=True)
                    self._verification(page,cancel,progress,deadline)
                    page.wait_for_timeout(400)
                raise RuntimeError('提交后未取得结果，请查询原记录；禁止直接重发')
            finally:
                # Keep the page available to the user on ambiguity, but disarm it.
                self.pending=None
        return self.session.execute(action,cancel,progress)
    def _find_receipt(self,page,receipt):
        """Require an exact titled video link; navigation alone is only UNKNOWN."""
        if not receipt.get('item_id'):
            return {'accepted':False,'visibility':'UNKNOWN','review':'UNKNOWN','reason':'尚无可靠作品ID；页面跳转不能当作发布成功，请补录ID后核对'}
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            matches=[]
            links=page.locator('a[href*="douyin.com/video/"]')
            for index in range(links.count()):
                link=links.nth(index)
                href=link.get_attribute('href') or '';url=urlsplit(href)
                match=re.fullmatch(r'/video/([0-9]+)',url.path)
                if url.scheme!='https' or url.hostname not in ('www.douyin.com','douyin.com') or not match or not link.is_visible():continue
                if (link.inner_text() or link.get_attribute('title') or '').strip()!=receipt.get('title'):continue
                if receipt.get('item_id') and receipt['item_id']!=match.group(1):continue
                matches.append({'item_id':match.group(1),'candidate_url':'https://www.douyin.com/video/'+match.group(1),
                                'title':receipt['title'],'accepted':True,'visibility':'UNKNOWN','review':'UNKNOWN',
                                'verified_by':'exact_title_and_id_link_in_account_content_list'})
            unique={x['item_id']:x for x in matches}
            if len(unique)==1:return next(iter(unique.values()))
            if len(unique)>1:break
            page.wait_for_timeout(400)
        return {'accepted':False,'visibility':'UNKNOWN','review':'UNKNOWN','reason':'作品列表未提供可唯一核对的标题与作品ID'}
    def query(self,receipt,account,cancel,progress):
        def action(session,cancel,progress):
            self._check_account(session,account,cancel,progress)
            page=session._page
            page.goto(MANAGE_URL,wait_until='domcontentloaded',timeout=30000)
            return self._find_receipt(page,receipt)
        return self.session.execute(action,cancel,progress)
    def discard(self,receipt,cancel,progress):
        def action(session,cancel,progress):
            if self.pending and self.pending['token']==receipt.get('token'):
                try:self.pending['page'].goto('about:blank',wait_until='commit',timeout=5000)
                except Exception:pass
                self.pending=None
            return None
        return self.session.execute(action,cancel,progress)
