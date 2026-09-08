# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path
import os,sys
import PySide6

base=Path(SPECPATH)
# Resolve dependencies against Python, Qt and Windows, never unrelated tools
# on the developer's PATH (for example Poppler's incompatible ICU DLL).
windows=Path(os.environ.get('SystemRoot','C:/Windows'))
os.environ['PATH']=os.pathsep.join([sys.base_prefix,str(Path(sys.base_prefix)/'DLLs'),
    str(Path(PySide6.__file__).parent),str(windows/'System32'),str(windows)])
mp_data,mp_bins,mp_hidden=collect_all('mediapipe')
ff_data,ff_bins,ff_hidden=collect_all('imageio_ffmpeg')
a=Analysis([str(base/'studio.py')],pathex=[str(base)],
    binaries=mp_bins+ff_bins,
    datas=mp_data+ff_data+[(str(base/'models'),'models'),(str(base/'assets'),'assets'),(str(base/'THIRD_PARTY'),'THIRD_PARTY')],
    hiddenimports=mp_hidden+ff_hidden,
    hookspath=[],hooksconfig={},runtime_hooks=[str(base/'startup_log.py')],
    excludes=['tkinter','pytest','IPython','jupyter','matplotlib.tests','PySide6.QtWebEngineCore','PySide6.QtWebEngineWidgets','PySide6.QtQml','PySide6.QtQuick'],noarchive=False)
# Windows supplies its ICU API. A conda/poppler ICU DLL with the same basename
# exports version-suffixed symbols and must never replace the system library.
a.binaries=[entry for entry in a.binaries if Path(entry[0]).name.lower() not in ('icuuc.dll','icudt78.dll')]
# Qt's platforminputcontexts hook collects the optional GPL-only virtual
# keyboard. This QWidget application does not use it. Keep ordinary Windows
# text input; exclude this plugin and its dedicated library from distribution.
a.binaries=[entry for entry in a.binaries if 'virtualkeyboard' not in entry[0].lower()]
a.datas=[entry for entry in a.datas if 'virtualkeyboard' not in entry[0].lower()
         and not entry[0].lower().endswith(('.pyc','.pyo'))]
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='影像工作台',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='影像工作台')
