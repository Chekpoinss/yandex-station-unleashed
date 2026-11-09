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


class AdbPcmPlayer:
    """Play a local file through ADB without a cloud account or LAN stream port.

    Old station adbd versions corrupt binary shell streams, so transfer a WAV
    using ADB sync and let aplay read the finite file on the speaker.
    """
    def __init__(self, adb, ffmpeg, source, offset, pcm, duration=None):
        import subprocess
        import tempfile
        import secrets
        from pathlib import Path
        self.adb = adb
        token = secrets.token_hex(12)
        self.pidfile = '/tmp/panel_pcm_' + token + '.pid'
        self.remote = '/data/quasar/webpanel/music-cache/' + token + '.wav'
        self.pcm = Path(pcm)
        self.player = None
        self.log = tempfile.TemporaryFile()
        self.closed = False
        options = {'creationflags': getattr(subprocess, 'CREATE_NO_WINDOW', 0)}
        try:
            with tempfile.TemporaryDirectory() as folder:
                wav = Path(folder) / 'audio.wav'
                args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin',
                        '-ss', str(float(offset)), '-i', source, '-filter_complex',
                        '[0:a:0]asplit=2[play][lights]',
                        '-map', '[play]', '-vn', '-ac', '2', '-ar', '48000',
                        '-c:a', 'pcm_s16le', '-y', str(wav), '-map', '[lights]',
                        '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-y', str(pcm)]
                result = subprocess.run(args, capture_output=True, timeout=300, **options)
                if result.returncode:
                    raise ValueError('FFmpeg: ' + result.stderr.decode('utf-8', 'replace')[-400:])
                adb.shell('mkdir -p /data/quasar/webpanel/music-cache')
                adb.push(wav, self.remote)
            self.player = subprocess.Popen(adb._base() + ['shell',
                f'aplay -q --process-id-file {self.pidfile} {self.remote}; r=$?; '
                f'rm -f {self.remote} {self.pidfile}; printf \"\\nPCM_RESULT:%d\\n\" \"$r\"'],
                stdin=subprocess.DEVNULL, stdout=self.log, stderr=self.log, **options)
        except Exception:
            self.close()
            raise

    def poll(self):
        if self.player.poll() is None:
            return None
        self.log.seek(0)
        return 0 if b'PCM_RESULT:0' in self.log.read() else 1

    def error(self):
        self.log.seek(0)
        detail = self.log.read().decode('utf-8', 'replace').strip()[-400:]
        return 'Не удалось воспроизвести файл через adb' + (': ' + detail if detail else '')

    def close(self):
        import subprocess
        if self.closed:
            return
        self.closed = True
        try:
            # Check the unique pidfile in aplay argv before stopping our process.
            self.adb.shell(f'p=$(cat {self.pidfile} 2>/dev/null); '
                           f'if [ -n \"$p\" ] && grep -q {self.pidfile} /proc/$p/cmdline 2>/dev/null; '
                           f'then kill \"$p\" 2>/dev/null; fi; rm -f {self.remote} {self.pidfile}; true', check=False)
        except Exception:
            pass  # A disconnected speaker must not prevent local process cleanup.
        if self.player is not None:
            if self.player.poll() is None:
                self.player.terminate()
            try:
                self.player.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.player.kill()
                self.player.wait(timeout=3)
        self.log.close()
        self.pcm.unlink(missing_ok=True)
