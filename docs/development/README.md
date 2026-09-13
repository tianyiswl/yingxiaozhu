# 开发验证

基础回归：`python -m unittest discover -s tests`，先创建 `.tmp` 目录。

界面专项需设置 `P009_UI_TEST=1` 与 `QT_QPA_PLATFORM=offscreen`，可运行 `python -m unittest tests.test_daily_ui tests.test_settings_fixes`。

源码公开前本地基础回归148项：97通过、51项按条件跳过；当前工作台与设置24项界面检查通过。没有执行真实账号发布。

需要 FFmpeg、真实浏览器或平台账号的测试不是默认测试的一部分；跳过不代表已验收。请勿提交生成的测试图片、数据库、账号会话和日志。
