"""Focused synchronous port of the user-owned 一键发 DouYinVideo form logic.
Origin: maintainer-owned 一键发 project; see THIRD_PARTY_NOTICES.md.
Only field preparation/readback is ported; this class cannot click Publish.
"""
import re
import sys

class TopicEntityMissing(RuntimeError):
    pass

class OneClickForm:

    def _visible_title_inputs(self, page):
        """只定位作品标题，排除定时发布等其他文本输入框。"""
        selectors = ("input[placeholder*='作品标题']:visible", "input[placeholder*='填写标题']:visible", "input[placeholder*='标题']:visible")
        for selector in selectors:
            candidates = self._visible_enabled_items(page.locator(selector))
            if candidates:
                return candidates
        return []

    def clear_platform_title(self, page):
        title_inputs = []
        for attempt in range(30):
            title_inputs = self._visible_title_inputs(page)
            if title_inputs:
                break
            if attempt < 29:
                self._wait(page, 500)
        if len(title_inputs) != 1:
            raise RuntimeError(f'抖音独立标题输入框数量异常：{len(title_inputs)}，已停止填写')
        title = (self.title or '').strip()
        title_inputs[0].fill('')
        actual_after_clear = title_inputs[0].input_value().strip()
        if actual_after_clear:
            raise RuntimeError(f'抖音标题未能清空旧值，已停止填写：{actual_after_clear}')
        title_inputs[0].fill(title)
        actual_title = title_inputs[0].input_value().strip()
        if actual_title != title:
            raise RuntimeError(f'抖音标题写入后不一致：期望={title}，实际={actual_title}')
        pass

    def _fill_editor_body(self, page, editor, body):
        """清空后写入作品文案，并确认编辑器没有保留旧内容。

        Windows 端的抖音富文本编辑器偶尔会吞掉 ``fill("")`` 的清空事件，
        导致新输入的文案追加在旧内容之后。这里通过键盘全选删除和两次页面回读
        确认写入结果；有限重试后仍不一致才停止流程，避免保存错误草稿。
        """
        expected_body = self._normalize_body_text(body)
        lines = str(body or '').replace('\r\n', '\n').replace('\r', '\n').split('\n')
        for attempt in range(1, 4):
            editor.fill('')
            editor.click(force=True)
            page.keyboard.press('Meta+A' if sys.platform == 'darwin' else 'Control+A')
            page.keyboard.press('Backspace')
            self._wait(page, 250)
            actual_after_clear = self._normalize_body_text(self._read_raw_editor_text(editor))
            if actual_after_clear:
                if attempt < 3:
                    pass
                    continue
                raise RuntimeError('抖音详情编辑器未能清空旧文案，已停止同步以避免内容重复')
            for (index, line) in enumerate(lines):
                if line:
                    page.keyboard.insert_text(line)
                if index < len(lines) - 1:
                    page.keyboard.press('Enter')
            self._wait(page, 350)
            actual_after_write = self._normalize_body_text(self._read_raw_editor_text(editor))
            if actual_after_write == expected_body:
                return
            if attempt < 3:
                pass
                continue
            raise RuntimeError(f'抖音详情写入后回读不一致，已停止同步以避免内容重复；期望={expected_body!r}，实际={actual_after_write!r}')

    @staticmethod
    def _normalize_topic_mention(value):
        return ''.join(str(value or '').replace('\u200b', '').split()).lstrip('#')

    def _read_topic_mentions(self, editor):
        values = editor.locator('[data-mention="#"], [data-mention="activity"]').all_text_contents()
        return [self._normalize_topic_mention(value) for value in values if self._normalize_topic_mention(value)]

    def _read_platform_topics(self, editor):
        """只回读平台已转换的话题实体，普通 #文字一律忽略。"""
        return self._read_topic_mentions(editor)

    def _read_raw_editor_text(self, editor):
        span_values = editor.locator('span[data-string="true"]').all_text_contents()
        if any(('\u200b' in value for value in span_values)):
            return ''.join((value.replace('\u200b', '\n') for value in span_values))
        return editor.evaluate('\n            node => {\n              const mentions = [...node.querySelectorAll(\n                \'[data-mention="#"], [data-mention="activity"]\'\n              )];\n              const previous = mentions.map(item => item.style.display);\n              try {\n                mentions.forEach(item => { item.style.display = \'none\'; });\n                return node.innerText;\n              } finally {\n                mentions.forEach((item, index) => { item.style.display = previous[index]; });\n              }\n            }\n            ')

    def _find_unique_topic_candidate(self, page, tag_name):
        for attempt in range(self.TOPIC_CANDIDATE_WAIT_ATTEMPTS):
            candidates = page.locator('span[class*="tag-hash-view-name"]')
            exact_matches = []
            for index in range(candidates.count()):
                candidate = candidates.nth(index)
                try:
                    if candidate.is_visible() and candidate.inner_text().strip() == tag_name:
                        exact_matches.append(candidate)
                except Exception:
                    continue
            if len(exact_matches) == 1:
                return exact_matches[0]
            if len(exact_matches) > 1:
                raise RuntimeError(f'抖音话题“{tag_name}”出现多个可见的精确候选，已停止选择')
            if attempt < self.TOPIC_CANDIDATE_WAIT_ATTEMPTS - 1:
                self._wait(page, 500)
        raise TopicEntityMissing(f'抖音未返回话题“{tag_name}”的精确平台候选')

    def _add_platform_topic(self, page, editor, tag_name):
        add_controls = self._visible_enabled_items(page.get_by_text('#添加话题', exact=True))
        if len(add_controls) != 1:
            raise RuntimeError(f'抖音“#添加话题”入口数量异常：{len(add_controls)}，已停止选择“{tag_name}”')
        before_topics = self._read_platform_topics(editor)
        add_controls[0].click(timeout=5000)
        editor.press_sequentially(tag_name, delay=50)
        candidate = self._find_unique_topic_candidate(page, tag_name)
        candidate.click(timeout=5000)
        for attempt in range(10):
            topics = self._read_platform_topics(editor)
            if topics == [*before_topics, tag_name]:
                return
            if attempt < 9:
                self._wait(page, 300)
        raise TopicEntityMissing(f'抖音话题“{tag_name}”点选官方候选后未形成平台话题实体')

    @staticmethod
    def _normalize_body_text(value):
        text = str(value or '').replace('\u200b', '').replace('\r\n', '\n').replace('\r', '\n')
        lines = [line.replace('\xa0', ' ').rstrip() for line in text.split('\n')]
        while lines and (not lines[0]):
            lines.pop(0)
        while lines and (not lines[-1]):
            lines.pop()
        return '\n'.join(lines)

    def verify_prepublish_form(self, page, require_covers=True):
        editor = page.locator('.zone-container').first
        editor.wait_for(state='visible', timeout=15000)
        expected_body = self.description if self.description is not None else self.title
        expected_topics = [str(tag).strip().lstrip('#') for tag in self.tags if str(tag).strip().lstrip('#')]
        raw_text = self._read_raw_editor_text(editor)
        body_text = raw_text
        if '#' in body_text:
            raise RuntimeError('抖音详情中仍存在手写 # 文本，不能保存草稿')
        expected_normalized = self._normalize_body_text(expected_body)
        actual_normalized = self._normalize_body_text(body_text)
        if expected_normalized != actual_normalized:
            raise RuntimeError(f'抖音详情回读与发布包不一致，不能保存草稿；期望={expected_normalized!r}，实际={actual_normalized!r}')
        topics = self._read_platform_topics(editor)
        if topics != expected_topics:
            raise RuntimeError(f'抖音平台话题回读不一致：期望={expected_topics}，实际={topics}')
        expected_cover_ratios = [ratio for ratio in ('4:3', '3:4') if self.thumbnail_paths.get(ratio)]
        if self.save_draft_only and require_covers and expected_cover_ratios:
            actual_cover_ratios = (self.cover_verification or {}).get('ratios') or []
            if actual_cover_ratios != expected_cover_ratios:
                raise RuntimeError(f'抖音双封面回读不完整：期望={expected_cover_ratios}，实际={actual_cover_ratios}')
            self.verify_cover_persistence(page, expected_cover_ratios)
        title_confirmed = None
        if self.description is not None:
            title_inputs = self._visible_title_inputs(page)
            if len(title_inputs) != 1:
                raise RuntimeError(f'抖音标题输入框数量异常：{len(title_inputs)}，不能保存草稿')
            actual_title = title_inputs[0].input_value().strip()
            expected_title = (self.title or '').strip()
            if actual_title != expected_title:
                raise RuntimeError(f'抖音标题回读不一致：期望={expected_title}，实际={actual_title}')
            title_confirmed = True
        return {'title_confirmed': title_confirmed, 'detail_confirmed': True, 'topics_confirmed': topics, 'topic_entry_method': 'platform_candidate_entity_readback', 'covers_confirmed': list(expected_cover_ratios) if require_covers else []}

    @staticmethod
    def _visible_enabled_items(locator):
        items = []
        for index in range(locator.count()):
            item = locator.nth(index)
            try:
                if item.is_visible() and item.is_enabled():
                    items.append(item)
            except Exception:
                continue
        return items

    @staticmethod
    def _visible_items(locator):
        """返回可见节点，不把验证码按钮的初始禁用态误判为控件不存在。"""
        items = []
        for index in range(locator.count()):
            item = locator.nth(index)
            try:
                if item.is_visible():
                    items.append(item)
            except Exception:
                continue
        return items

    TOPIC_CANDIDATE_WAIT_ATTEMPTS = 12

    def __init__(self, content, cancel):
        self.title = content['title']
        self.description = content['body']
        self.tags = content['topics']
        self.cancel = cancel
        self.thumbnail_paths = {}
        self.save_draft_only = False

    def _wait(self, page, milliseconds):
        from ..core import Runner
        Runner(self.cancel).check()
        page.wait_for_timeout(milliseconds)
        Runner(self.cancel).check()

    def fill(self, page):
        self.clear_platform_title(page)
        editors = self._visible_items(page.locator('.zone-container'))
        if len(editors) != 1:
            raise RuntimeError('抖音正文编辑器无法唯一确认')
        editor = editors[0]
        self._fill_editor_body(page, editor, self.description)
        for topic in self.tags:
            self._add_platform_topic(page, editor, topic)
        return self.verify_prepublish_form(page, require_covers=False)
