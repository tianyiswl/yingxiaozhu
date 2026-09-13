import tempfile,unittest
from unittest.mock import Mock,patch
from factory_video_tool.browser_login import DouyinBrowserSession
from factory_video_tool.repository import Repository

class BrowserPageLifecycleTests(unittest.TestCase):
    def test_keeps_last_page_instead_of_closing_browser(self):
        page=Mock();page.is_closed.return_value=False
        context=Mock();context.pages=[page]
        self.assertIs(DouyinBrowserSession._select_browser_page(context),page)
        page.close.assert_not_called();context.new_page.assert_not_called()

    def test_closes_only_extra_pages(self):
        pages=[Mock(),Mock()]
        for page in pages:page.is_closed.return_value=False
        context=Mock();context.pages=pages
        self.assertIs(DouyinBrowserSession._select_browser_page(context),pages[0])
        pages[0].close.assert_not_called();pages[1].close.assert_called_once()

    def test_retry_failed_initialization_and_recover_stale_page(self):
        with tempfile.TemporaryDirectory() as root:
            session=DouyinBrowserSession(Repository(root))
            stale=Mock();stale.is_closed.return_value=False;stale.evaluate.side_effect=RuntimeError('Disconnected')
            session._page=stale
            bad=Mock();bad.pages=[];bad.new_page.side_effect=RuntimeError('Target.createTarget')
            page=Mock();page.is_closed.return_value=False
            good=Mock();good.pages=[page]
            playwright=Mock();playwright.chromium.launch_persistent_context.side_effect=[bad,good]
            with patch('playwright.sync_api.sync_playwright') as start,patch.object(session,'_dispose'):
                start.return_value.start.return_value=playwright
                session._ensure_browser()
            self.assertIs(session._page,page);bad.close.assert_called_once()
            self.assertEqual(playwright.chromium.launch_persistent_context.call_count,2)
