"""Capture startup diagnostics before Qt imports in the windowed portable app."""
import os,sys
from pathlib import Path
try:
    folder=Path(os.environ.get('LOCALAPPDATA',str(Path.home())))/'FacePrivacyStudio'
    folder.mkdir(parents=True,exist_ok=True)
    stream=(folder/'last-error.log').open('w',encoding='utf8',buffering=1)
    sys.stderr=stream
    if sys.stdout is None:sys.stdout=stream
except OSError:
    pass
