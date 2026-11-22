"""Windows Spatial Sound static speaker objects on an already configured endpoint."""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack
import time
from uuid import UUID

import numpy as np
from audio_layout import WaveFormat, Guid, guid, com_method, endpoint_client, check_hresult

# WAVE speaker bit -> AudioObjectType (the two enums use different channel orders).
OBJECTS = {1:2,2:4,4:8,8:16,16:128,32:256,256:1<<17,512:32,1024:64,
           4096:512,16384:1024,32768:2048,131072:4096}
PTR = C.c_void_p
OUT = C.POINTER(PTR)
UINT_OUT = C.POINTER(W.UINT)


class ActivationParams(C.Structure):
    _pack_ = 1
    _fields_ = [('format',C.POINTER(WaveFormat)),('mask',W.UINT),('minimum',W.UINT),
                ('maximum',W.UINT),('category',C.c_int),('event',W.HANDLE),('notify',PTR)]


class BlobVariant(C.Structure):
    _fields_ = [('vt',W.WORD),('reserved',W.WORD*3),('size',W.ULONG),('data',PTR)]


def active_spatial_format(endpoint):
    # Windows can accept static objects and downmix them even when Spatial Sound
    # is off. Check the user's active format before advertising virtual speakers.
    rt = C.windll.combase
    with ExitStack() as local:
        result = rt.RoInitialize(1)
        if result >= 0:local.callback(rt.RoUninitialize)
        elif result & 0xffffffff != 0x80010106:check_hresult(result)
        def string(text):
            value = PTR()
            check_hresult(rt.WindowsCreateString(W.LPCWSTR(text),len(text),C.byref(value)))
            local.callback(rt.WindowsDeleteString,value)
            return value
        factory = PTR()
        iid = guid('3ec37f7b-936d-4e04-9728-2827d9f758c4')
        name = string('Windows.Media.Audio.SpatialAudioDeviceConfiguration')
        check_hresult(rt.RoGetActivationFactory(name,C.byref(iid),C.byref(factory)))
        local.callback(com_method(factory,2),factory)
        config = PTR()
        check_hresult(com_method(factory,6,PTR,OUT)(factory,string(endpoint),C.byref(config)))
        local.callback(com_method(config,2),config)
        active = PTR()
        check_hresult(com_method(config,9,OUT)(config,C.byref(active)))
        local.callback(rt.WindowsDeleteString,active)
        rt.WindowsGetStringRawBuffer.restype = W.LPCWSTR
        value = rt.WindowsGetStringRawBuffer(active,None)
        return bool(value and UUID(value).int)


def spatial_client(endpoint, cleanup):
    client = endpoint_client(endpoint,'bbf8e066-aaaa-49be-9a4d-fd2a858ea27f',cleanup)
    try:
        active = active_spatial_format(endpoint)
    except (OSError,AttributeError,ValueError):
        count = W.UINT()
        check_hresult(com_method(client,5,UINT_OUT)(client,C.byref(count)))
        active = count.value > 0
    if not active:return client,0
    native = W.UINT()
    check_hresult(com_method(client,4,UINT_OUT)(client,C.byref(native)))
    # Use the established 7.1.4 virtual speaker bed. A back-center object is
    # available as well on some renderers but belongs to a different bed.
    mask = sum(speaker for speaker,obj in OBJECTS.items() if native.value & obj)
    if mask & 0x630 == 0x630:mask &= ~256
    return client,mask


class SpatialStream:
    backend = 'Windows Spatial Sound'

    def __init__(self,client,mask):
        self.mask = mask
        self.objects = {}
        self.addresses = []
        self.updating = False
        self.last_end = 0.
        self.kernel = C.windll.kernel32
        self.kernel.CreateEventW.restype = W.HANDLE
        format = WaveFormat(3,1,48000,192000,4,32,0)  # mono IEEE float per object
        supported = com_method(client,8,C.POINTER(WaveFormat))(client,C.byref(format))
        if supported != 0:raise OSError('Unsupported spatial object format')
        with ExitStack() as local:
            self.event = self.kernel.CreateEventW(None,False,False,None)
            if not self.event:raise C.WinError()
            local.callback(self.kernel.CloseHandle,W.HANDLE(self.event))
            object_mask = sum(obj for bit,obj in OBJECTS.items() if mask & bit)
            params = ActivationParams(C.pointer(format),object_mask,0,0,7,self.event,None)
            variant = BlobVariant(vt=65,size=C.sizeof(params),data=C.addressof(params))  # VT_BLOB
            stream = PTR()
            iid = guid('bab5f473-b423-477b-85f5-b5a332a04153')
            activate = com_method(client,10,C.POINTER(BlobVariant),C.POINTER(Guid),OUT)
            check_hresult(activate(client,C.byref(variant),C.byref(iid),C.byref(stream)))
            self.stream = stream
            local.callback(com_method(stream,2),stream)
            check_hresult(com_method(stream,5)(stream))
            local.callback(com_method(stream,6),stream)
            self.begin = com_method(stream,8,UINT_OUT,UINT_OUT)
            self.end = com_method(stream,9)
            self.activate = com_method(stream,10,W.UINT,OUT)
            self.cleanup = local.pop_all()
        self.deadline = time.monotonic()+1.

    def available(self):
        ready = self.kernel.WaitForSingleObject(W.HANDLE(self.event),10)
        if ready == 258 and time.monotonic()<self.deadline:return 0
        if ready != 0:raise OSError('空间音频设备停止请求数据')
        self.deadline = time.monotonic()+1.
        count,frames = W.UINT(),W.UINT()
        check_hresult(self.begin(self.stream,C.byref(count),C.byref(frames)))
        self.updating = True
        self.addresses.clear()
        if not frames.value:
            self.end_update()
            return 0
        for bit,kind in OBJECTS.items():
            if not self.mask & bit:continue
            if bit not in self.objects:
                obj = PTR()
                check_hresult(self.activate(self.stream,kind,C.byref(obj)))
                self.cleanup.callback(com_method(obj,2),obj)
                self.objects[bit] = (obj,com_method(obj,3,OUT,UINT_OUT))
            obj,get_buffer = self.objects[bit]
            address,size = PTR(),W.UINT()
            check_hresult(get_buffer(obj,C.byref(address),C.byref(size)))
            if not address or size.value < frames.value*4:raise OSError('空间音频缓冲区长度无效')
            self.addresses.append(address)
        return frames.value

    def end_update(self):
        self.updating = False
        check_hresult(self.end(self.stream))

    def write(self, block):
        planes = np.ascontiguousarray(block.T,dtype=np.float32)
        planes *= 1/32768
        for samples,address in zip(planes,self.addresses):
            C.memmove(address,samples.ctypes.data,samples.nbytes)
        self.end_update()
        self.last_end = time.monotonic()+len(block)/48000

    def drained(self):
        return time.monotonic()>=self.last_end

    def close(self):
        if self.updating:self.end(self.stream)
        self.cleanup.close()
        self.endpoint_scope.close()
