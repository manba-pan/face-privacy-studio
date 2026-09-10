"""FFV1 export that edits native planes and verifies every encoded video frame.

The contract is source sample preservation outside the mask footprint, not a
bit-identical container. Chroma footprints include every touched shared sample.
Whole clips only; Matroska timestamps are retained to its millisecond precision.
"""
import hashlib
import math
from pathlib import Path
import re
import tempfile
from fractions import Fraction
import av
import cv2
import numpy as np
import core
import color_management as colors


def supported(fmt):
    return bool(re.fullmatch(r'(?:yuv(?:420|422|444)p(?:(?:9|10|12|14|16)le)?|gbrp(?:9|10|12|14|16)le)', fmt))


def validate_source(media):
    if not supported(media.get('pixel_format', '')):
        raise ValueError('源像素无损支持平面 YUV（8～16 位）和 GBR（9～16 位）。此格式请选 RGB 无损编码。')
    if media.get('rotation') or media.get('sar', '1') not in ('1', '1/1') or media.get('interlaced'):
        raise ValueError('源像素无损暂支持逐行、方形像素且无旋转标记的素材；此素材不会被自动变换。')
    if media.get('alpha') or media.get('hdr') or media.get('wide_gamut'):
        raise ValueError('源像素无损目前支持无透明通道的 SDR 素材。')


def compatible(media):
    try:
        validate_source(media)
    except ValueError:
        return False
    return True


def validate(info, options, media):
    validate_source(media)
    if options.resolution or options.fps or options.rotation:
        raise ValueError('源像素无损要求保持原尺寸、原帧率、原方向；需要变换时请选择 RGB 无损编码或普通导出。')
    if options.start_frame or options.end_frame not in (-1, info.frames):
        raise ValueError('源像素无损本版导出完整原片。请点击“恢复整段”，或选择其他导出方式截取。')
    if options.audio not in ('copy', 'mute') or options.volume != 1:
        raise ValueError('源像素无损请保留原音轨或移除音轨；调整音量请用其他导出方式。')
    if options.input_color != 'auto' or options.input_range != 'auto' or options.output_color != 'preserve':
        raise ValueError('源像素无损保留原始色彩标记，请把输入设为自动、输出设为保留源色彩。')


def mask_for(width, height, faces, settings, manual, index, auto_mask):
    mask = np.zeros((height, width), np.uint8)
    if auto_mask and not faces and settings.missing == 'full_frame': mask[:] = 255
    if auto_mask:
        for face in faces:
            polygon = core.face_polygon(face, width, height, settings)
            cv2.fillPoly(mask, [np.round(polygon).astype(np.int32)], 255)
    for box in manual or []:
        if box['start'] <= index <= box['end']:
            x0, y0, x1, y1 = np.array(core.manual_rect(box, index)) * [width, height, width, height]
            cv2.fillPoly(mask, [np.round([[x0,y0],[x1,y0],[x1,y1],[x0,y1]]).astype(np.int32)], 255)
    return mask


def plane_mask(mask, width, height):
    h, w = mask.shape
    sx, sy = math.ceil(w / width), math.ceil(h / height)
    padded = np.pad(mask, ((0, height*sy-h), (0, width*sx-w)))
    return padded.reshape(height, sy, width, sx).max(axis=(1, 3))


def plane_array(plane, bits):
    dtype = np.dtype('<u2') if bits > 8 else np.dtype('u1')
    padded = np.frombuffer(plane, dtype).reshape(plane.height, plane.line_size // dtype.itemsize)
    return padded, padded[:, :plane.width]


def edit_plane(values, mask, settings, black):
    # Separate connected masks so people far apart do not share one huge effect.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    strength = max(1, min(5, int(settings.strength)))
    for component in range(1, count):
        x, y, w, h, _ = stats[component]
        roi = values[y:y+h, x:x+w]
        selected = labels[y:y+h, x:x+w] == component
        if settings.style == 'solid': filtered = np.full_like(roi, black)
        elif settings.style == 'blur':
            sigma = max(2., min(w,h) * [.045,.075,.12,.19,.30][strength-1])
            filtered = cv2.GaussianBlur(roi, (0,0), sigma)
        else:
            block = max(3, round(min(w,h) * [.045,.08,.13,.21,.34][strength-1]))
            small = cv2.resize(roi, (max(1,w//block), max(1,h//block)), interpolation=cv2.INTER_AREA)
            filtered = cv2.resize(small, (w,h), interpolation=cv2.INTER_NEAREST)
        roi[selected] = filtered[selected]


def render(frame, mask, settings):
    if not np.any(mask):
        return frame
    # Decoded H.264/HEVC frames can share buffers with reference pictures.
    # Detach BEFORE obtaining planes; copying a NumPy array alone is not enough.
    frame.make_writable()
    bits = max(c.bits for c in frame.format.components)
    for i, plane in enumerate(frame.planes):
        padded, values = plane_array(plane, bits)
        copy = padded.copy()
        pmask = plane_mask(mask, plane.width, plane.height)
        chroma = not frame.format.is_rgb and i in (1, 2)
        black = (1 << (bits-1)) if chroma else (0 if frame.format.is_rgb or frame.color_range == 2 else 16 << (bits-8))
        edit_plane(copy[:, :plane.width], pmask, settings, black)
        plane.update(copy.tobytes())
    return frame


def digest_frame(frame, digest):
    bits = max(c.bits for c in frame.format.components)
    for plane in frame.planes:
        _, values = plane_array(plane, bits)
        digest.update(values.tobytes())


def copy_frame(frame):
    """Keep the cached original untouched when preview settings change."""
    result = av.VideoFrame(frame.width, frame.height, frame.format.name)
    bits = max(c.bits for c in frame.format.components)
    for source, target in zip(frame.planes, result.planes):
        padded, values = plane_array(target, bits)
        padded[:] = 0
        values[:] = plane_array(source, bits)[1]
    for key in ('color_primaries', 'color_trc', 'colorspace', 'color_range'):
        setattr(result, key, getattr(frame, key))
    result.pts, result.time_base = frame.pts, frame.time_base
    return result


class PreviewReader:
    """One decoder and one unmodified frame, owned only by the preview worker."""
    def __init__(self, info):
        self.info = info
        self.container = av.open(info.path)
        self.stream = self.container.streams.video[0]
        self.frames = None
        self.frame = None
        self.index = None

    def close(self):
        self.frames = self.frame = None
        self.container.close()

    def read(self, index, cancel=None):
        core.check_cancel(cancel)
        if self.index == index:
            return self.frame
        rate = Fraction(self.info.fps_text)
        target = Fraction(index, 1) / rate
        offset = (self.stream.start_time or 0) * self.stream.time_base
        threshold = target - Fraction(1, 2) / rate
        # Nearby forward requests reuse the decoder; large/backward jumps seek.
        if self.index is None or not 0 < index-self.index <= 2*self.info.fps:
            self.container.seek(int((target+offset)/self.stream.time_base), stream=self.stream, backward=True)
            self.frames = iter(self.container.decode(self.stream))
            self.frame = None
        while True:
            core.check_cancel(cancel)
            frame = self.frame
            if frame is not None and frame.pts is not None and frame.pts*frame.time_base-offset >= threshold:
                if not supported(frame.format.name) or frame.rotation or frame.interlaced_frame:
                    raise ValueError('此帧不支持原生预览，请切换 RGB 无损编码。')
                self.index = index
                return frame
            self.frame = next(self.frames, None)
            if self.frame is None:
                self.index = None
                raise ValueError('无法读取此位置的原生画面。')

    def preview(self, index, faces, settings, manual, auto_mask, limit, cancel=None):
        frame = self.read(index, cancel)
        mask = mask_for(frame.width, frame.height, faces, settings, manual, index, auto_mask)
        # Render at source precision/size, then resize only the displayed RGB.
        output = render(copy_frame(frame), mask, settings) if np.any(mask) else frame
        rgb = output.to_ndarray(format='rgb24')
        size = core.fit_size(frame.width, frame.height, limit)
        return cv2.resize(rgb, size, interpolation=cv2.INTER_AREA) if size != (frame.width, frame.height) else rgb


def preview(info, index, faces, settings, manual, auto_mask, limit):
    validate_source(colors.probe(info.path))
    reader = PreviewReader(info)
    try:
        return reader.preview(index, faces, settings, manual, auto_mask, limit)
    finally:
        reader.close()


def verify_outside_mask(original, output, mask):
    if (original.width, original.height, original.format.name) != (output.width, output.height, output.format.name):
        raise RuntimeError('原片与成片像素格式或尺寸不一致，未保存输出。')
    bits = max(c.bits for c in original.format.components)
    for source, target in zip(original.planes, output.planes):
        before, after = plane_array(source, bits)[1], plane_array(target, bits)[1]
        outside = plane_mask(mask, source.width, source.height) == 0
        if not np.array_equal(before[outside], after[outside]):
            raise RuntimeError('未遮挡区域与独立解码的原片像素不一致，未保存输出。')


def export(analysis, destination, settings, options, manual=None, cancel=None, progress=None, media=None):
    info = analysis.info
    validate(info, options, media)
    source, dest = Path(info.path).resolve(), Path(destination).resolve()
    if dest == source or dest.exists(): raise ValueError('请选择尚不存在的新文件名，原视频不会覆盖。')
    if dest.suffix.lower() != '.mkv': raise ValueError('源像素无损使用 MKV 文件。')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.native-', suffix='.mkv', dir=dest.parent, delete=False) as temp_file:
        temp = Path(temp_file.name)
    expected = hashlib.sha256()
    complete = False
    try:
        core.check_cancel(cancel)
        with av.open(str(source)) as inp, av.open(str(temp), 'w', format='matroska', options={'avoid_negative_ts':'disabled'}) as out:
            vs = inp.streams.video[0]
            dst = out.add_stream('ffv1', rate=vs.average_rate or Fraction(info.fps_text))
            dst.width, dst.height, dst.pix_fmt = vs.width, vs.height, media['pixel_format']
            dst.time_base = vs.time_base
            dst.codec_context.time_base = vs.time_base
            dst.codec_context.thread_count = 4
            dst.codec_context.options = {'level': '3', 'coder': '1', 'context': '1', 'slicecrc': '1'}
            for key, value in media['color'].items(): setattr(dst.codec_context, key, value)
            dst.metadata.update(vs.metadata)
            audio = {s.index: out.add_stream_from_template(s) for s in inp.streams.audio} if options.audio == 'copy' else {}
            audio_hashes = {s: hashlib.sha256() for s in audio}
            audio_times = {s: [] for s in audio}
            out.metadata.update(inp.metadata)
            selected_streams = [vs] + [s for s in inp.streams.audio if s.index in audio]
            frame_count = 0
            first_time = None
            last_time = None
            # Store only hashes of timing/audio, never pixels or all frames.
            times_path = temp.with_suffix('.times.tmp')
            with times_path.open('w', encoding='ascii') as times:
                for packet in inp.demux(selected_streams):
                    core.check_cancel(cancel)
                    if packet.stream.index == vs.index:
                        for frame in packet.decode():
                            if frame.pts is None: raise ValueError('素材缺少逐帧时间戳，无法进行源像素无损导出。')
                            timestamp = frame.pts * frame.time_base
                            if first_time is None: first_time = timestamp
                            if last_time is not None and timestamp <= last_time: raise ValueError('素材时间戳不递增，已停止导出。')
                            if frame.format.name != media['pixel_format'] or (frame.width,frame.height) != (vs.width,vs.height):
                                raise ValueError('视频中途改变像素格式或尺寸，已停止导出。')
                            for key, value in media['color'].items():
                                current = int(getattr(frame,key))
                                if current != (0 if key == 'color_range' else 2) and current != value:
                                    raise ValueError('视频中途改变色彩标记，已停止导出。')
                                setattr(frame,key,value)
                            if options.auto_mask or manual:
                                if abs(float(timestamp-first_time)-frame_count/info.fps) > .51/info.fps:
                                    raise ValueError('此变帧率素材与当前识别时间线不一致，无法保证遮挡对齐。请用普通导出；无效果时可保留源像素。')
                            faces = analysis.faces[frame_count] if options.auto_mask and frame_count < len(analysis.faces) else []
                            if options.auto_mask and frame_count >= len(analysis.faces): raise ValueError('识别结果未覆盖全部原始帧。')
                            mask = mask_for(frame.width, frame.height, faces, settings, manual, frame_count, options.auto_mask)
                            render(frame, mask, settings)
                            digest_frame(frame, expected)
                            times.write(str(float(timestamp)) + '\n')
                            last_time = timestamp
                            for encoded in dst.encode(frame): out.mux(encoded)
                            frame_count += 1
                            if progress and frame_count % 5 == 0: progress(min(.8, .8*frame_count/max(1, info.frames)),frame_count,0)
                    elif packet.dts is not None:
                        source_index = packet.stream.index
                        audio_hashes[source_index].update(bytes(packet))
                        stamp = float((packet.pts if packet.pts is not None else packet.dts)*packet.time_base)
                        if not audio_times[source_index]: audio_times[source_index].append(stamp)
                        audio_times[source_index][1:] = [stamp]
                        packet.stream = audio[packet.stream.index]
                        out.mux(packet)
                for encoded in dst.encode(): out.mux(encoded)
        if not frame_count: raise ValueError('未读到视频帧。')
        actual = hashlib.sha256()
        with av.open(str(temp)) as check, av.open(str(source)) as reference, times_path.open(encoding='ascii') as times:
            originals = iter(reference.decode(video=0))
            actual_count = 0
            for frame in check.decode(video=0):
                core.check_cancel(cancel)
                if frame.format.name != media['pixel_format']: raise RuntimeError('无损导出改变了像素格式。')
                expected_time = times.readline()
                if not expected_time or abs(float(frame.pts*frame.time_base)-float(expected_time)) > .0011:
                    raise RuntimeError('无损导出时间戳校验失败。')
                original = next(originals, None)
                if original is None or original.pts is None or abs(float(original.pts*original.time_base)-float(expected_time)) > .0011:
                    raise RuntimeError('原片独立解码时间线校验失败。')
                faces = analysis.faces[actual_count] if options.auto_mask else []
                mask = mask_for(original.width, original.height, faces, settings, manual, actual_count, options.auto_mask)
                verify_outside_mask(original, frame, mask)
                digest_frame(frame, actual)
                actual_count += 1
                if progress and actual_count % 5 == 0: progress(.8+.2*actual_count/frame_count,actual_count,0)
            if next(originals, None) is not None or times.readline() or actual_count != frame_count or actual.digest() != expected.digest():
                raise RuntimeError('无损导出逐帧像素校验失败，成片未保存。')
            if len(check.streams.audio) != len(audio): raise RuntimeError('音轨数量校验失败。')
        with av.open(str(temp)) as check:
            hashes = {s.index: hashlib.sha256() for s in check.streams.audio}
            stamps = {s.index: [] for s in check.streams.audio}
            if hashes:
                for packet in check.demux(check.streams.audio):
                    core.check_cancel(cancel)
                    if packet.dts is None: continue
                    key = packet.stream.index
                    hashes[key].update(bytes(packet))
                    stamp = float((packet.pts if packet.pts is not None else packet.dts)*packet.time_base)
                    if not stamps[key]: stamps[key].append(stamp)
                    stamps[key][1:] = [stamp]
            for source_index, output_stream in audio.items():
                key = output_stream.index
                if hashes[key].digest() != audio_hashes[source_index].digest():
                    raise RuntimeError('原音轨内容校验失败，成片未保存。')
                if len(stamps[key]) != len(audio_times[source_index]) or any(abs(a-b)>.0011 for a,b in zip(stamps[key],audio_times[source_index])):
                    raise RuntimeError('原音轨时间戳校验失败，成片未保存。')
        if colors.probe(temp)['color'] != media['color']:
            raise RuntimeError('源色彩标记校验失败，成片未保存。')
        core.check_cancel(cancel)
        if core.fingerprint(source) != analysis.fingerprint or dest.exists(): raise RuntimeError('源视频或目标位置发生变化。')
        temp.rename(dest)
        complete = True
        analysis.stats.update(export_encoder='源像素无损 · FFV1 · 逐帧校验通过', native_verified_frames=frame_count,
                              native_source_verified_frames=frame_count)
        return str(dest)
    finally:
        temp.with_suffix('.times.tmp').unlink(missing_ok=True)
        if not complete: temp.unlink(missing_ok=True)
