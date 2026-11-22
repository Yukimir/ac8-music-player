"""Play into the largest usable speaker layout on the Windows default endpoint."""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack
import threading

from audio_layout import (STEREO, WAVE_MAPPER, Guid, check_hresult, com_method, endpoint_client, guid,
                          layout_candidates, layout_name, mix_mask, pcm_format, preferred_endpoint)
from spatial_output import SpatialStream, spatial_client
from surround import SurroundMix


class WaveHeader(C.Structure):
    _fields_ = [('data',C.c_void_p),('size',W.DWORD),('recorded',W.DWORD),('user',C.c_size_t),
                ('flags',W.DWORD),('loops',W.DWORD),('next',C.c_void_p),('reserved',C.c_size_t)]


class WaveStream:
    backend = 'PCM'
    frames = 960

    def __init__(self, mm, device, mask):
        self.mm, self.mask = mm, mask
        self.handle = W.HANDLE()
        self.prepared = []
        self.index = 0
        fmt = pcm_format(mask)
        self.check(mm.waveOutOpen(C.byref(self.handle),device,C.byref(fmt),0,0,0))
        try:
            self.buffers = [C.create_string_buffer(self.frames*mask.bit_count()*2) for _ in range(4)]
            for buf in self.buffers:
                h = WaveHeader(C.addressof(buf),len(buf))
                self.check(mm.waveOutPrepareHeader(self.handle,C.byref(h),C.sizeof(h)))
                self.prepared.append(h)
        except BaseException:
            self.close()
            raise

    def check(self, result):
        if result:
            message = C.create_unicode_buffer(256)
            self.mm.waveOutGetErrorTextW(result,message,256)
            raise OSError(message.value or f'Windows 音频错误 {result}')

    def available(self):
        return 0 if self.prepared[self.index].flags & 0x10 else self.frames

    def write(self, block):
        h = self.prepared[self.index]
        if block.nbytes != h.size:raise ValueError('音频块与设备格式不匹配')
        C.memmove(h.data,block.ctypes.data,h.size)
        self.check(self.mm.waveOutWrite(self.handle,C.byref(h),C.sizeof(h)))
        self.index = (self.index+1) % len(self.prepared)

    def drained(self):
        return all(not h.flags & 0x10 for h in self.prepared)

    def close(self):
        self.mm.waveOutReset(self.handle)
        for h in self.prepared:self.mm.waveOutUnprepareHeader(self.handle,C.byref(h),C.sizeof(h))
        self.mm.waveOutClose(self.handle)


class WasapiStream:
    """Shared PCM for endpoints whose legacy WinMM path rejects their full layout."""
    backend = 'WASAPI PCM'

    def __init__(self,endpoint,mask):
        self.mask = mask
        fmt = pcm_format(mask)
        ptr = C.c_void_p
        with ExitStack() as cleanup:
            client = endpoint_client(endpoint,'1cb9ad4c-dbfa-4c32-b178-c2f568a703b2',cleanup)
            init = com_method(client,3,C.c_int,W.DWORD,C.c_int64,C.c_int64,ptr,ptr)
            # Permit system sample-rate and sample-format conversion for this speaker mask.
            check_hresult(init(client,0,0x88000000,600000,0,C.byref(fmt),None))
            capacity = W.UINT()
            check_hresult(com_method(client,4,C.POINTER(W.UINT))(client,C.byref(capacity)))
            render = ptr()
            iid = guid('f294acfc-3146-4483-a7bf-addca7c260e2')
            check_hresult(com_method(client,14,C.POINTER(Guid),C.POINTER(ptr))(client,C.byref(iid),C.byref(render)))
            cleanup.callback(com_method(render,2),render)
            self.client,self.render,self.capacity = client,render,capacity.value
            self.get_padding = com_method(client,6,C.POINTER(W.UINT))
            self.get_buffer = com_method(render,3,W.UINT,C.POINTER(ptr))
            self.release = com_method(render,4,W.UINT,W.DWORD)
            self.started = False
            self.cleanup = cleanup.pop_all()

    def padding(self):
        value = W.UINT()
        check_hresult(self.get_padding(self.client,C.byref(value)))
        return value.value

    def available(self):
        free = self.capacity-self.padding()
        frames = min(960,self.capacity)
        return frames if free>=frames else 0

    def write(self,block):
        memory = C.c_void_p()
        check_hresult(self.get_buffer(self.render,len(block),C.byref(memory)))
        C.memmove(memory,block.ctypes.data,block.nbytes)
        check_hresult(self.release(self.render,len(block),0))
        if not self.started:
            check_hresult(com_method(self.client,10)(self.client))
            self.started = True

    def drained(self):
        try:return self.padding()==0
        except OSError:return True

    def close(self):
        if self.started:com_method(self.client,11)(self.client)
        self.cleanup.close()


def open_stream(mm, allow_spatial=True):
    device,endpoint = preferred_endpoint(mm)
    with ExitStack() as cleanup:
        try:pcm = mix_mask(endpoint) if endpoint else STEREO
        except OSError:pcm = STEREO
        candidates = [(mask,False) for mask in layout_candidates(pcm)]
        if allow_spatial and endpoint:
            try:
                client,native = spatial_client(endpoint,cleanup)
                candidates.extend((mask,True) for mask in layout_candidates(native) if mask.bit_count()>2)
            except OSError:pass
        # Stable sort resolves ties in favor of direct PCM on the same device.
        candidates.sort(key=lambda item:item[0].bit_count(),reverse=True)
        for mask,spatial in candidates:
            try:
                if not spatial:
                    try:return WaveStream(mm,device,mask)
                    except OSError:
                        if endpoint:return WasapiStream(endpoint,mask)
                        continue
                stream = SpatialStream(client,mask)
                stream.endpoint_scope = cleanup.pop_all()
                return stream
            except OSError:continue
    # The default may have changed between discovery and open.
    return WaveStream(mm,WAVE_MAPPER,STEREO)


class Output:
    def __init__(self,transport,lock,on_error,on_block=None):
        self.player = transport
        self.lock = lock
        self.on_error = on_error
        self.stop = threading.Event()
        self.flush = threading.Event()
        self.on_block = on_block
        self.layout = ''
        self.backend = ''
        self.thread = threading.Thread(target=self._run,daemon=True)
        self.thread.start()

    def _run(self):
        mm = C.WinDLL('winmm')
        stream = None
        mixer = None
        allow_spatial = True
        try:
            while not self.stop.is_set():
                with self.lock:
                    playing = self.player.playing
                    flush = self.flush.is_set()
                    self.flush.clear()
                if stream and (flush or (not playing and stream.drained())):
                    stream.close()
                    stream = None
                if not playing:
                    allow_spatial = True
                    self.stop.wait(.012)
                    continue
                try:
                    if stream is None:
                        stream = open_stream(mm,allow_spatial)
                        mixer = SurroundMix(stream.mask) if stream.mask != STEREO else None
                        with self.lock:
                            self.layout,self.backend = layout_name(stream.mask),stream.backend
                    frames = stream.available()
                    if not frames:
                        self.stop.wait(.001)
                        continue
                    with self.lock:
                        if not self.player.playing or self.flush.is_set():
                            # Close also completes an outstanding spatial buffer transaction.
                            stream.close()
                            stream = None
                            continue
                        if mixer:
                            block = self.player.render(frames,spatial=mixer)
                            monitor = mixer.monitor
                        else:
                            block = monitor = self.player.render(frames)
                        if self.on_block:self.on_block(monitor)
                    stream.write(block)
                except OSError as exc:
                    was_spatial = isinstance(stream,SpatialStream)
                    if stream:stream.close()
                    stream = None
                    if was_spatial:
                        allow_spatial = False
                        continue
                    with self.lock:self.player.playing = False
                    self.on_error(str(exc))
        except Exception as exc:
            with self.lock:self.player.playing = False
            self.on_error(str(exc))
        finally:
            if stream:stream.close()

    def close(self):
        self.stop.set()
        self.thread.join(3)
