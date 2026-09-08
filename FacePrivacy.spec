# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all
from pathlib import Path

base=Path(SPECPATH)
mp_data,mp_bins,mp_hidden=collect_all('mediapipe')
ff_data,ff_bins,ff_hidden=collect_all('imageio_ffmpeg')
a=Analysis([str(base/'app.py')],pathex=[str(base)],
    binaries=mp_bins+ff_bins,
    datas=mp_data+ff_data+[(str(base/'models'),'models'),(str(base/'THIRD_PARTY'),'THIRD_PARTY')],
    hiddenimports=mp_hidden+ff_hidden,
    hookspath=[],hooksconfig={},runtime_hooks=[],
    excludes=['pytest','IPython','jupyter','matplotlib.tests'],noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,[],exclude_binaries=True,name='采访打码助手',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,console=False,
    disable_windowed_traceback=False)
coll=COLLECT(exe,a.binaries,a.datas,strip=False,upx=False,name='采访打码助手')
