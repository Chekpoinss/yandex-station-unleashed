import array
import functools
import math
import pathlib
import shutil
import subprocess
import tempfile
import threading
import unittest
import wave
from http.server import SimpleHTTPRequestHandler,ThreadingHTTPServer

from audio_stream import command
from music_spectrum import fft_bands


class AudioStreamTests(unittest.TestCase):
    def test_network_and_local_seek_have_different_input_semantics(self):
        network=command('ffmpeg','https://example.test/stream.m3u8',True,'http://127.0.0.1:9999',90,'lights.pcm')
        self.assertNotIn('-ss',network[:network.index('-i')])
        self.assertIn('atrim=start=90.0',network[network.index('-filter_complex')+1])
        local=command('ffmpeg','track.mp3',False,'',90,'lights.pcm')
        self.assertLess(local.index('-ss'),local.index('-i'))
        self.assertIn('atrim=start=0',local[local.index('-filter_complex')+1])

    def test_http_seek_produces_matching_mp3_and_pcm_from_the_selected_part(self):
        bundled=pathlib.Path(__file__).parents[1]/'runtime'/'ffmpeg.exe'
        ffmpeg=str(bundled) if bundled.exists() else shutil.which('ffmpeg')
        if not ffmpeg:self.skipTest('FFmpeg unavailable')
        with tempfile.TemporaryDirectory() as folder:
            folder=pathlib.Path(folder)
            samples=array.array('h',[round(12000*math.sin(2*math.pi*(80 if i<16000 else 2500)*i/8000)) for i in range(40000)])
            with wave.open(str(folder/'source.wav'),'wb') as out:
                out.setnchannels(1);out.setsampwidth(2);out.setframerate(8000);out.writeframes(samples.tobytes())
            class Handler(SimpleHTTPRequestHandler):
                def log_message(self,*args):pass
            server=ThreadingHTTPServer(('127.0.0.1',0),functools.partial(Handler,directory=str(folder)))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            pcm=folder/'lights.pcm'
            try:
                result=subprocess.run(command(ffmpeg,f'http://127.0.0.1:{server.server_port}/source.wav',True,'',3,pcm),
                    capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.assertEqual(result.returncode,0,result.stderr.decode('utf8','replace'))
                self.assertGreater(len(result.stdout),10000)
                self.assertAlmostEqual(pcm.stat().st_size/16000,2,delta=.02)
                levels=fft_bands(pcm.read_bytes()[:1600])
                self.assertEqual(levels.index(max(levels)),2) # selected tail, not the bass intro
            finally:
                server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
