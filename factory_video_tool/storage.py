"""Portable storage selection and verified, non-destructive migration."""
import json,os,shutil,sqlite3,sys,uuid
from contextlib import closing
from pathlib import Path
from .models import file_hash

def program_dir():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]

def config_path():return program_dir()/'storage.json'

def load_config(path,base):
    value=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    for key in ('data_dir','migrate_from'):
        if value.get(key) and not Path(value[key]).is_absolute():value[key]=str(base/value[key])
    return value

def read_config():
    return load_config(config_path(),program_dir())

def choose_existing_directory(start,message):
    from PySide6.QtWidgets import QFileDialog,QMessageBox
    QMessageBox.information(None,'沿用已有数据',message)
    selected=QFileDialog.getExistingDirectory(None,'选择正在使用的数据文件夹（包含 app.sqlite）',str(start))
    if not selected:raise ValueError('未选择数据目录，已停止启动，原数据未改动')
    root=Path(selected).resolve()
    if not (root/'app.sqlite').is_file():raise ValueError('所选目录没有 app.sqlite，请选择原来的 data 数据文件夹')
    return root

def resolve_storage():
    # A newly extracted package is an independent installation. Never discover
    # test workspaces or cross-version pointers from the user's system folders.
    config=read_config()
    if config.get('data_dir'):return config,True
    local=program_dir()/'data'
    return {'data_dir':str(local)},(local/'app.sqlite').is_file()

def writable(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    probe=root/('.write-check-'+uuid.uuid4().hex)
    try:probe.write_bytes(b'')
    finally:probe.unlink(missing_ok=True)

def save_config(value):
    value=dict(value)
    if value.get('data_dir'):
        try:value['data_dir']=str(Path(value['data_dir']).resolve().relative_to(program_dir()))
        except ValueError:pass
    path=config_path();temp=path.with_suffix('.json.partial')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');temp.replace(path)

def migrate(source,destination,progress=lambda _:None):
    from PySide6.QtCore import QLockFile
    source=Path(source).resolve();destination=Path(destination).resolve()
    if source==destination:return
    if source in destination.parents or destination in source.parents:raise ValueError('新旧数据目录不能互相包含')
    if destination.exists() and any(destination.iterdir()):raise ValueError('目标数据目录不是空目录，请选择新的目录')
    locks=[];stage=destination.parent/('.migration-'+uuid.uuid4().hex)
    try:
        for name in ('app.lock','daily-window.lock'):
            lock=QLockFile(str(source/name));lock.setStaleLockTime(0)
            if not lock.tryLock(0):raise ValueError('旧客户端或后台仍在运行，请完全退出后重试')
            locks.append(lock)
        stage.mkdir(parents=True)
        for path in source.rglob('*'):
            relative=path.relative_to(source)
            progress(str(relative))
            if path.name.endswith('.lock') or path.name.startswith('Singleton'):continue
            if path.is_symlink():raise ValueError('旧目录包含链接文件，请先确认其真实位置：'+str(relative))
            target=stage/relative
            if path.is_dir():target.mkdir(parents=True,exist_ok=True)
            elif path.is_file():
                target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
                if file_hash(path)!=file_hash(target):raise ValueError('迁移校验失败：'+str(relative))
        def remap(value):
            if isinstance(value,str):
                if value==str(source) or value.startswith(str(source)+os.sep):return str(destination)+value[len(str(source)):]
                if os.path.isabs(value):
                    try:return str(destination/Path(value).resolve().relative_to(source))
                    except (ValueError,OSError):pass
                return value
            if isinstance(value,list):return [remap(v) for v in value]
            if isinstance(value,dict):return {k:remap(v) for k,v in value.items()}
            return value
        dbpath=stage/'app.sqlite'
        if dbpath.exists():
            with closing(sqlite3.connect(dbpath)) as db, db:
                if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('迁移后的数据库校验失败')
                for table in ['settings','jobs','publications','library_materials','library_bgm','library_contents','daily_items','daily_batches']:
                    if not db.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?',(table,)).fetchone():continue
                    columns=[r[1] for r in db.execute('PRAGMA table_info('+table+')')]
                    for row in db.execute('SELECT rowid,* FROM '+table).fetchall():
                        for column,value in zip(columns,row[1:]):
                            if not isinstance(value,str):continue
                            try:decoded=json.loads(value);updated=json.dumps(remap(decoded),ensure_ascii=False) if remap(decoded)!=decoded else value
                            except (ValueError,TypeError):updated=remap(value)
                            if updated!=value:db.execute('UPDATE '+table+' SET '+column+'=? WHERE rowid=?',(updated,row[0]))
                db.execute("DELETE FROM settings WHERE key IN ('daily_worker','daily_worker_error','output_dir','temp_dir','log_dir')")
        (stage/'migration.json').write_text(json.dumps({'source':str(source),'verified':True,'original_preserved':True},ensure_ascii=False),encoding='utf-8')
        if destination.exists():destination.rmdir()
        stage.replace(destination)
    finally:
        for lock in locks:lock.unlock()
        if stage.exists():shutil.rmtree(stage)

def startup_storage(explicit=None):
    from PySide6.QtWidgets import QFileDialog,QMessageBox
    if explicit:return Path(explicit).resolve()
    config,known=resolve_storage();root=Path(config['data_dir']).resolve()
    if config.get('migrate_from') and not (Path(config['migrate_from'])/'app.sqlite').is_file():
        raise ValueError('待迁移的原数据目录无法访问，请连接原硬盘后重试；不会创建空数据。')
    if known and not config.get('migrate_from') and not (root/'app.sqlite').is_file():
        root=choose_existing_directory(root.parent,'之前的数据目录无法访问：'+str(root)+'。请先连接原硬盘，或选择现有数据目录；不会自动改用旧数据或新建空库。')
    try:writable(root)
    except OSError:
        if config.get('migrate_from'):raise ValueError('迁移目标不可写，请恢复目标磁盘或在旧客户端重新选择数据位置')
        QMessageBox.information(None,'选择数据目录','程序目录不可写，请选择其他磁盘上的可写文件夹。本次不会改用系统盘。')
        if known:
            root=choose_existing_directory(root.parent,'原数据目录不可写，请选择可写的现有数据目录。');writable(root)
            selected=None
        else:selected=QFileDialog.getExistingDirectory(None,'选择数据保存目录')
        if not known:
            if not selected:raise ValueError('尚未选择可写的数据目录')
            root=Path(selected)/'data';writable(root)
        try:save_config({'data_dir':str(root)})
        except OSError:QMessageBox.information(None,'本次数据目录','程序目录不可写，无法保存目录选择；下次启动需重新选择同一目录。建议把整个程序移到可写文件夹。')
    source=config.get('migrate_from')
    if source and not (root/'app.sqlite').exists():
        from PySide6.QtWidgets import QProgressDialog,QApplication
        from PySide6.QtCore import Qt
        dialog=QProgressDialog('正在迁移并校验旧数据，原数据会保留…',None,0,0)
        dialog.setWindowTitle('迁移数据');dialog.setWindowModality(Qt.WindowModality.ApplicationModal);dialog.setMinimumDuration(0);dialog.show()
        import time
        last=[0.0]
        def progress(name):
            if time.monotonic()-last[0]>.15:
                dialog.setLabelText('正在复制并校验：'+name);QApplication.processEvents();last[0]=time.monotonic()
        try:migrate(source,root,progress)
        finally:dialog.close()
        QMessageBox.information(None,'迁移完成','数据已复制并通过校验。旧数据仍保留在：'+str(source)+'\n确认新版本正常后再清理旧目录。')
    from .repository import Repository
    repo=Repository(root)
    for key,folder in [('output_dir','outputs'),('temp_dir','temp'),('log_dir','logs')]:
        location=Path(repo.setting(key,str(root.parent/folder)))
        writable(location);repo.save_setting(key,str(location))
    try:save_config({'data_dir':str(root)})
    except OSError:pass
    import tempfile
    tempfile.tempdir=repo.setting('temp_dir');os.environ['TMPDIR']=tempfile.tempdir;os.environ['TMP']=tempfile.tempdir;os.environ['TEMP']=tempfile.tempdir
    return root
