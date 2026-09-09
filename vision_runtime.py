"""Load vision without initializing MediaPipe's optional PortAudio recorder.

The application uses Qt/FFmpeg for audio and never records microphones.
AudioRecord supports a missing sounddevice dependency. Avoiding that optional
import prevents a Windows PortAudio shutdown deadlock during Qt playback.
"""
import importlib
import sys
import threading

_lock=threading.Lock()


def mediapipe():
    with _lock:
        if 'mediapipe' in sys.modules:return sys.modules['mediapipe']
        absent='sounddevice' not in sys.modules
        if absent:sys.modules['sounddevice']=None
        try:return importlib.import_module('mediapipe')
        finally:
            if absent and sys.modules.get('sounddevice') is None:sys.modules.pop('sounddevice',None)
