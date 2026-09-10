"""Small, explicit SDR color policy; native preservation never guesses tags."""
from dataclasses import dataclass
import av

SPACES = {'bt709': ('Rec.709 · 高清 SDR', 1, 1, 1),
          'smpte170m': ('Rec.601 · NTSC', 6, 6, 6),
          'bt470bg': ('Rec.601 · PAL', 5, 5, 5)}
PRIMARIES = {1: 'bt709', 5: 'bt470bg', 6: 'smpte170m', 9: 'bt2020'}
TRANSFERS = {1: 'bt709', 4: 'gamma22', 5: 'gamma28', 6: 'smpte170m',
             13: 'iec61966-2-1', 14: 'bt2020-10', 15: 'bt2020-12',
             16: 'smpte2084', 18: 'arib-std-b67'}
MATRICES = {0: 'gbr', 1: 'bt709', 5: 'bt470bg', 6: 'smpte170m', 9: 'bt2020nc'}


def probe(path):
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        ctx = stream.codec_context
        tags = {key: int(getattr(ctx, key))
                for key in ('color_primaries', 'color_trc', 'colorspace', 'color_range')}
        frame = next(container.decode(stream))
        for key in tags:
            value = int(getattr(frame, key, tags[key]))
            if value != (0 if key == 'color_range' else 2): tags[key] = value
        bits = max(c.bits for c in frame.format.components)
        return {'color': tags, 'pixel_format': frame.format.name, 'bit_depth': bits,
                'ten_bit': bits > 8,
                'hdr': tags['color_trc'] in (16, 18),
                'wide_gamut': tags['color_primaries'] == 9,
                'rotation': frame.rotation,
                'sar': str(stream.sample_aspect_ratio or '1'),
                'audio_tracks': len(container.streams.audio),
                'alpha': any(c.is_alpha for c in frame.format.components),
                'interlaced': bool(frame.interlaced_frame),
                'video_description': f'{ctx.name} / {frame.format.name}',
                'audio_codec': container.streams.audio[0].codec_context.name if container.streams.audio else '',
                'audio_description': ' / '.join(s.codec_context.name for s in container.streams.audio)}


@dataclass(frozen=True)
class Color:
    primaries: str
    transfer: str
    matrix: str
    range: str


def source_color(media, options):
    tags = media.get('color', {})
    override = options.input_color
    fallback = 'bt709'  # Visible in the UI; never used in native preservation.
    if override != 'auto':
        if override not in SPACES: raise ValueError('未知输入色彩空间')
        _, p, t, m = SPACES[override]
        color = Color(PRIMARIES[p], TRANSFERS[t], MATRICES[m], 'tv')
    else:
        color = Color(PRIMARIES.get(tags.get('color_primaries'), fallback),
                      TRANSFERS.get(tags.get('color_trc'), fallback),
                      MATRICES.get(tags.get('colorspace'), fallback), 'tv')
    detected_range = 'pc' if tags.get('color_range') == 2 or media.get('pixel_format', '').startswith(('rgb', 'bgr', 'gbr', 'yuvj')) else 'tv'
    value_range = detected_range if options.input_range == 'auto' else options.input_range
    if value_range not in ('tv', 'pc'): raise ValueError('未知输入电平范围')
    return Color(color.primaries, color.transfer, color.matrix, value_range)


def output_color(media, options):
    source = source_color(media, options)
    if options.output_color == 'preserve': return source
    if options.output_color not in SPACES: raise ValueError('未知输出色彩空间')
    _, p, t, m = SPACES[options.output_color]
    return Color(PRIMARIES[p], TRANSFERS[t], MATRICES[m], source.range)


def description(media):
    tags = media.get('color', {})
    p = PRIMARIES.get(tags.get('color_primaries'), '未标记')
    t = TRANSFERS.get(tags.get('color_trc'), '未标记')
    m = MATRICES.get(tags.get('colorspace'), '未标记')
    r = {1: '有限范围', 2: '全范围'}.get(tags.get('color_range'), '范围未标记')
    return f'{media.get("bit_depth", "?")} 位 · {media.get("pixel_format", "未知格式")}\n原色 {p} · 传递 {t}\n矩阵 {m} · {r}'


def policy_note(media, options):
    if options.profile == 'native':
        note = '保留源像素、位深及已有色彩标记；未标记项原样保留。'
        if media.get('audio_codec') == 'aac' and options.audio == 'copy':
            note += ' AAC 原压缩包保留，但跨封装的预填充／起止样本边界可能不同。'
        return note
    tags = media.get('color', {})
    unknown = any(tags.get(k, 2) == 2 for k in ('color_primaries', 'color_trc', 'colorspace'))
    note = '未完整标记色彩：普通导出按 Rec.709 解释，可手动修正。' if unknown and options.input_color == 'auto' else ''
    if options.output_color != 'preserve': note += ' 色彩转换会改变像素，不属于源像素无损。'
    return note or '输出沿用输入的原色、传递曲线和电平范围。'


def validate(media, options):
    if options.input_color not in ('auto', *SPACES) or options.output_color not in ('preserve', *SPACES):
        raise ValueError('未知色彩空间设置')
    if options.input_range not in ('auto', 'tv', 'pc'): raise ValueError('未知电平设置')
    if media.get('alpha'): raise ValueError('此素材带透明通道，本版尚不能保留 Alpha，已停止导出。')
    if media.get('hdr') or media.get('wide_gamut'):
        raise ValueError('已识别 HDR / 广色域素材。本版先支持 SDR 色彩控制；不会静默转换或错标为 Rec.709。')
    if options.profile != 'native':
        source = source_color(media, options)
        if source.transfer not in ('bt709', 'smpte170m', 'gamma28', 'iec61966-2-1'):
            raise ValueError('此传递曲线尚未验证，请先在专业软件中受控转换为 SDR。')


def decode_filter(media, options):
    """Convert actual samples (not just tags) before any RGB mask processing."""
    source, target = source_color(media, options), output_color(media, options)
    # colorspace works on planar YUV. Packed RGB first uses an explicit matrix.
    start = f'scale=in_range={source.range}:out_range={source.range}:out_color_matrix={source.matrix if source.matrix != "gbr" else "bt709"},format=yuv444p16le'
    matrix = source.matrix if source.matrix != 'gbr' else 'bt709'
    target_matrix = target.matrix if target.matrix != 'gbr' else 'bt709'
    transform = (f'colorspace=ispace={matrix}:iprimaries={source.primaries}:itrc={source.transfer}:irange={source.range}'
                 f':space={target_matrix}:primaries={target.primaries}:trc={target.transfer}:range={target.range}:format=yuv444p12')
    # Skip the colorspace filter when no gamut/transfer conversion is required.
    if (source.primaries, source.transfer) == (target.primaries, target.transfer):
        return f'scale=in_color_matrix={matrix}:in_range={source.range}:out_range=pc'
    return start + ',' + transform + f',scale=in_color_matrix={target_matrix}:in_range={target.range}:out_range=pc'


def encode_filter(media, options):
    color = output_color(media, options)
    return f'scale=in_range=pc:out_color_matrix={color.matrix if color.matrix != "gbr" else "bt709"}:out_range={color.range}'


def output_tags(media, options, rgb=False):
    color = output_color(media, options)
    return ['-color_primaries', color.primaries, '-color_trc', color.transfer,
            '-colorspace', 'rgb' if rgb else (color.matrix if color.matrix != 'gbr' else 'bt709'),
            '-color_range', 'pc' if rgb else color.range]


def tag_filter(media, options, rgb=False):
    color = output_color(media, options)
    matrix = 'gbr' if rgb else (color.matrix if color.matrix != 'gbr' else 'bt709')
    return (f'setparams=color_primaries={color.primaries}:color_trc={color.transfer}'
            f':colorspace={matrix}:range={"pc" if rgb else color.range}')


def verify_tags(path, media, options, rgb=False):
    color = output_color(media, options)
    expected = {'color_primaries':next(k for k,v in PRIMARIES.items() if v==color.primaries),
                'color_trc':next(k for k,v in TRANSFERS.items() if v==color.transfer),
                'colorspace':0 if rgb else next(k for k,v in MATRICES.items() if v==(color.matrix if color.matrix!='gbr' else 'bt709')),
                'color_range':2 if rgb or color.range=='pc' else 1}
    if probe(path)['color'] != expected:
        raise RuntimeError('成片色彩标记与所选输出不一致，未保存输出。')
