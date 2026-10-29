"""One aligned audio/LED graph; HLS must be decoded before trimming."""
import math


def command(ffmpeg, source, network, proxy, offset, pcm):
    offset = float(offset)
    if not math.isfinite(offset) or offset < 0:
        raise ValueError('Invalid stream offset')
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin']
    if network:
        args += ['-rw_timeout', '15000000']
        if proxy:
            args += ['-http_proxy', proxy]
    elif offset:
        args += ['-ss', str(offset)]
    args += ['-i', source]
    trim = offset if network else 0
    args += ['-filter_complex',
             f'[0:a:0]asetpts=PTS-STARTPTS,atrim=start={trim},asetpts=PTS-STARTPTS,asplit=2[play][lights]',
             '-map', '[play]', '-map_metadata', '-1', '-vn', '-ac', '2', '-ar', '48000',
             '-f', 'mp3', '-c:a', 'libmp3lame', '-b:a', '192k', '-write_xing', '0', 'pipe:1',
             '-map', '[lights]', '-map_metadata', '-1', '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le',
             '-flush_packets', '1', '-y', str(pcm)]
    return args
