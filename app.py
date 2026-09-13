import argparse
import sys
from PySide6.QtWidgets import QApplication,QMessageBox
from factory_video_tool.platforms import PathResolver
from factory_video_tool.repository import Repository

def main():
    if '--worker' in sys.argv:
        sys.argv.remove('--worker')
        from pathlib import Path
        import traceback
        root=Path(sys.argv[sys.argv.index('--workspace')+1]);root.mkdir(parents=True,exist_ok=True)
        logs=Path(Repository(root).setting('log_dir',str(root)));logs.mkdir(parents=True,exist_ok=True)
        with (logs/'background.log').open('a',encoding='utf-8') as log:
            sys.stdout=sys.stderr=log
            try:
                from factory_video_tool.batch_worker import main as worker_main
                return worker_main()
            except BaseException:
                traceback.print_exc();return 1
    parser=argparse.ArgumentParser();parser.add_argument('--workspace');parser.add_argument('--simulate',action='store_true');parser.add_argument('--legacy',action='store_true');args=parser.parse_args()
    if args.simulate and not args.workspace:parser.error('本地模拟需指定独立 --workspace')
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Nilangfeng.FactoryVideo.Desktop')
    app=QApplication(sys.argv)
    from pathlib import Path
    from PySide6.QtGui import QIcon
    app.setApplicationName('映小助')
    app.setWindowIcon(QIcon(str(Path(__file__).resolve().parent/'factory_video_tool/assets/app-icon.png')))
    try:
        from factory_video_tool.storage import startup_storage
        workspace=startup_storage(args.workspace)
        if args.legacy:
            from factory_video_tool.ui import Window
            window=Window(workspace)
        else:
            from factory_video_tool.daily_ui import DailyWindow
            window=DailyWindow(workspace,simulation=args.simulate)
        window.show()
    except Exception as e:QMessageBox.critical(None,'启动失败',str(e));return 1
    return app.exec()

if __name__=='__main__':sys.exit(main())
