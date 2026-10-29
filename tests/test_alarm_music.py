import hashlib
from pathlib import Path
import shlex
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import wave
from alarm_music import prepare


class AlarmMusicTests(unittest.TestCase):
    def setUp(self):
        ffmpeg=Path('runtime/ffmpeg.exe').resolve()
        if not ffmpeg.is_file():self.skipTest('FFmpeg is required for the real conversion fixture')
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.source=Path(self.temp.name)/'source.wav'
        with wave.open(str(self.source),'wb') as sound:
            sound.setparams((1,2,16000,0,'NONE','not compressed'));sound.writeframes(b'\0'*64000)
        self.files={};self.available=1024*1024
        def shell(command,**kwargs):
            words=shlex.split(command)
            if words[0]=='df':return (f'Filesystem 1K-blocks Used Available Use% Mounted\nroot 2000000 0 {self.available} 0% /data\n','',0)
            if words[0]=='sha256sum':
                data=self.files.get(words[1]);return ((hashlib.sha256(data).hexdigest()+' '+words[1]) if data else '', '',0)
            if words[0]=='mv':self.files[words[2]]=self.files.pop(words[1])
            if words[:2]==['rm','-f']:self.files.pop(words[2],None)
            return ('','',0)
        adb=SimpleNamespace(shell=Mock(side_effect=shell),push=Mock(side_effect=lambda path,target:self.files.__setitem__(target,Path(path).read_bytes())))
        panel=SimpleNamespace(adb=adb,ffmpeg=str(ffmpeg),alarm_media_lock=threading.RLock(),
                              alarms=lambda:{'installed':True,'dir':'/data/quasar/webpanel/alarm'})
        self.library=SimpleNamespace(panel=panel,lock=threading.RLock(),proxy='',remote={},
            data={'tracks':{'test':{'id':'test','title':'Test song','artist':'Artist','source':'local'}}},
            resolve=Mock(return_value=(str(self.source),False)))

    def test_real_conversion_is_stereo_48khz_wav_without_installing_scheduler(self):
        result=prepare(self.library,'test')
        target='/data/quasar/webpanel/alarm/'+result['sound']
        copy=Path(self.temp.name)/'copied.wav';copy.write_bytes(self.files[target])
        with wave.open(str(copy),'rb') as sound:
            self.assertEqual((sound.getnchannels(),sound.getframerate(),sound.getsampwidth()),(2,48000,2))
            self.assertEqual(sound.getnframes(),96000)
        commands=' '.join(c.args[0] for c in self.library.panel.adb.shell.call_args_list)
        self.assertNotIn('alarms.conf',commands);self.assertNotIn('mount ',commands);self.assertNotIn('init.d',commands)

    def test_identical_copy_reuses_remote_file_without_upload(self):
        first=prepare(self.library,'test');second=prepare(self.library,'test')
        self.assertEqual(first['sound'],second['sound']);self.assertTrue(second['cached'])
        self.library.panel.adb.push.assert_called_once()

    def test_low_space_does_not_upload_or_modify_schedule(self):
        self.available=0
        with self.assertRaisesRegex(ValueError,'места'):prepare(self.library,'test')
        self.library.panel.adb.push.assert_not_called();self.assertEqual(self.files,{})

    def test_cancelled_request_does_not_fetch_or_upload(self):
        with self.assertRaisesRegex(ValueError,'отменена'):prepare(self.library,'test',guard=lambda:False)
        self.library.resolve.assert_not_called();self.library.panel.adb.push.assert_not_called()

    def test_not_installed_fails_before_conversion(self):
        self.library.panel.alarms=lambda:{'installed':False}
        with self.assertRaisesRegex(ValueError,'установи'):prepare(self.library,'test')
        self.library.resolve.assert_not_called()
