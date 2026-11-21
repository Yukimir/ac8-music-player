"""Submit fixed-size PCM buffers to the Windows default audio device."""
import ctypes as C
from ctypes import wintypes as W
import threading
import time


class WaveFormat(C.Structure):
    _pack_=1
    _fields_=[('format',W.WORD),('channels',W.WORD),('rate',W.DWORD),('bytesPerSec',W.DWORD),
              ('align',W.WORD),('bits',W.WORD),('extra',W.WORD)]


class WaveHeader(C.Structure):
    _fields_=[('data',C.c_void_p),('size',W.DWORD),('recorded',W.DWORD),('user',C.c_size_t),
              ('flags',W.DWORD),('loops',W.DWORD),('next',C.c_void_p),('reserved',C.c_size_t)]


class Output:
    def __init__(self,transport,lock,on_error,on_block=None):
        self.player=transport
        self.lock=lock
        self.on_error=on_error
        self.stop=threading.Event()
        self.flush=threading.Event()
        self.on_block=on_block
        self.thread=threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def _run(self):
        mm=C.WinDLL('winmm')
        handle=W.HANDLE()
        fmt=WaveFormat(1,2,48000,192000,4,16,0)
        frames=960
        arrays=[C.create_string_buffer(frames*4) for _ in range(4)]
        headers=[WaveHeader(C.addressof(buf),len(buf)) for buf in arrays]
        size=C.sizeof(WaveHeader)
        opened=False
        prepared=[]
        def check(res):
            if res:
                msg=C.create_unicode_buffer(256)
                mm.waveOutGetErrorTextW(res,msg,256)
                raise RuntimeError(msg.value or f'Windows 音频错误 {res}')
        try:
            index=0
            while not self.stop.is_set():
                if opened and self.flush.is_set():
                    check(mm.waveOutReset(handle))
                    self.flush.clear()
                with self.lock: playing=self.player.playing
                if not playing:
                    self.stop.wait(.012)
                    continue
                if not opened:
                    try: check(mm.waveOutOpen(C.byref(handle),0xffffffff,C.byref(fmt),0,0,0))
                    except RuntimeError as err:
                        with self.lock:self.player.playing=False
                        self.on_error(str(err))
                        continue
                    opened=True
                    self.flush.clear()
                    for h in headers:
                        check(mm.waveOutPrepareHeader(handle,C.byref(h),size));prepared.append(h)
                h=headers[index]
                if h.flags&0x10:
                    self.stop.wait(.003)
                    continue
                with self.lock:
                    if not self.player.playing: continue
                    block=self.player.render(frames)
                    if self.on_block is not None:self.on_block(block)
                C.memmove(h.data,block.ctypes.data,frames*4)
                check(mm.waveOutWrite(handle,C.byref(h),size))
                index=(index+1)%len(headers)
        except Exception as exc:
            with self.lock:self.player.playing=False
            self.on_error(str(exc))
        finally:
            if opened:
                mm.waveOutReset(handle)
                for h in prepared:mm.waveOutUnprepareHeader(handle,C.byref(h),size)
                mm.waveOutClose(handle)

    def close(self):
        self.stop.set()
        self.thread.join(3)

