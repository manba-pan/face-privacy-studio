from importlib import metadata
from pathlib import Path
import shutil
import subprocess
import core

out=Path(__file__).parent/'THIRD_PARTY'
out.mkdir(exist_ok=True)
for name in ['mediapipe','opencv-contrib-python','numpy','pillow','imageio-ffmpeg','matplotlib','sounddevice','cffi','flatbuffers','absl-py','certifi','python-dateutil','six','contourpy','cycler','fonttools','kiwisolver','pyparsing','packaging','PySide6','PySide6_Essentials','PySide6_Addons','shiboken6','onnxruntime-directml','protobuf','sympy','mpmath']:
    dist=metadata.distribution(name)
    dest=out/name
    dest.mkdir(exist_ok=True)
    (dest/'METADATA.txt').write_text(dist.read_text('METADATA') or '',encoding='utf8')
    for f in dist.files or []:
        if any(word in Path(f).name.upper() for word in ['LICENSE','COPYING','NOTICE']) and '..' not in f.parts and Path(f).suffix.lower() not in ('.py','.pyc','.pyo'):
            source=Path(dist.locate_file(f))
            if source.is_file():
                target=dest/str(f).replace('/','_')
                shutil.copy2(source,target)
license_text=subprocess.run([core.ffmpeg(),'-L'],capture_output=True,creationflags=core.HIDDEN)
(out/'FFmpeg-LICENSE.txt').write_bytes(license_text.stdout+license_text.stderr)
(out/'SOURCES.txt').write_text('MediaPipe: https://github.com/google-ai-edge/mediapipe\nOpenCV: https://github.com/opencv/opencv\nFFmpeg: https://ffmpeg.org/\nFFmpeg binary distribution/builds: https://github.com/imageio/imageio-binaries\nPython: https://www.python.org/\nModel: https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task\n',encoding='utf8')
