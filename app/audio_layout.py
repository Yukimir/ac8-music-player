"""Match WinMM playback to the speaker mask of the same Windows audio endpoint."""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack
from uuid import UUID

STEREO = 0x3
LFE = 0x8
WAVE_MAPPER = 0xffffffff
WAVE_FORMAT_EXTENSIBLE = 0xfffe
Guid = C.c_ubyte * 16


def guid(value):
    return Guid.from_buffer_copy(UUID(value).bytes_le)


class WaveFormat(C.Structure):
    _pack_ = 1
    _fields_ = [('format', W.WORD), ('channels', W.WORD), ('rate', W.DWORD),
                ('bytesPerSec', W.DWORD), ('align', W.WORD), ('bits', W.WORD), ('extra', W.WORD)]


class ExtensibleFormat(C.Structure):
    _pack_ = 1
    _fields_ = [('wave', WaveFormat), ('validBits', W.WORD), ('mask', W.DWORD), ('subformat', Guid)]


def pcm_format(mask):
    channels = mask.bit_count()
    if mask == STEREO:
        return WaveFormat(1, 2, 48000, 192000, 4, 16, 0)
    wave = WaveFormat(WAVE_FORMAT_EXTENSIBLE, channels, 48000, 96000*channels, 2*channels, 16, 22)
    return ExtensibleFormat(wave, 16, mask, guid('00000001-0000-0010-8000-00aa00389b71'))


def layout_candidates(mask):
    # Stay within this endpoint's Windows configuration, including side vs back 5.1.
    if (mask & STEREO) != STEREO:
        return [STEREO]
    supported = mask & 0x3ffff
    layouts = [supported]
    for height in (0x2d000,0x5000,0x28000,0):
        for floor in (0x63f,0xff,0x70f,0x13f,0x60f,0x3f,0x603,0x33,0xf,STEREO):
            for lfe in (LFE,0):
                layout = ((floor & ~LFE) | lfe | height) & supported
                layouts.append(layout)
    layouts.extend([STEREO | (supported & LFE),STEREO])
    return sorted(dict.fromkeys(layouts),key=int.bit_count,reverse=True)


def layout_name(mask):
    heights = (mask & 0x3f800).bit_count()
    sub = int(bool(mask & LFE))
    return f'{mask.bit_count()-heights-sub}.{sub}' + (f'.{heights}' if heights else '')


def com_method(interface, index, *types):
    table = C.cast(interface, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(C.c_long, C.c_void_p, *types)(table[index])


def check_hresult(result):
    if result < 0:
        raise OSError(f'Windows audio endpoint error 0x{result & 0xffffffff:08x}')


def endpoint_client(endpoint_id, interface_id, cleanup):
    """Activate a COM audio interface on the selected device, owned by cleanup."""
    ole = C.windll.ole32
    pointer = C.c_void_p
    out_pointer = C.POINTER(pointer)
    result = ole.CoInitializeEx(None, 0)
    if result >= 0:
        cleanup.callback(ole.CoUninitialize)
    elif (result & 0xffffffff) != 0x80010106:  # An existing COM apartment is also usable.
        check_hresult(result)
    enumerator = pointer()
    clsid = guid('bcde0395-e52f-467c-8e3d-c4579291692e')
    iid_enum = guid('a95664d2-9614-4f35-a746-de8db63617e6')
    check_hresult(ole.CoCreateInstance(C.byref(clsid), None, 1, C.byref(iid_enum), C.byref(enumerator)))
    cleanup.callback(com_method(enumerator, 2), enumerator)
    device = pointer()
    get_device = com_method(enumerator, 5, W.LPCWSTR, out_pointer)
    check_hresult(get_device(enumerator, endpoint_id, C.byref(device)))
    cleanup.callback(com_method(device, 2), device)
    client = pointer()
    iid_client = guid(interface_id)
    activate = com_method(device, 3, C.POINTER(Guid), W.DWORD, pointer, out_pointer)
    check_hresult(activate(device, C.byref(iid_client), 1, None, C.byref(client)))
    cleanup.callback(com_method(client, 2), client)
    return client


def mix_mask(endpoint_id):
    with ExitStack() as cleanup:
        client = endpoint_client(endpoint_id,'1cb9ad4c-dbfa-4c32-b178-c2f568a703b2',cleanup)
        address = C.c_void_p()
        check_hresult(com_method(client,8,C.POINTER(C.c_void_p))(client,C.byref(address)))
        cleanup.callback(C.windll.ole32.CoTaskMemFree,address)
        wave = WaveFormat.from_address(address.value)
        if wave.format == WAVE_FORMAT_EXTENSIBLE and wave.extra >= 22:
            mask = ExtensibleFormat.from_address(address.value).mask
            if mask.bit_count() == wave.channels:
                return mask
        return STEREO


def preferred_endpoint(mm):
    # mmddk.h: DRVM_MAPPER_PREFERRED_GET, DRV_QUERYFUNCTIONINSTANCEIDSIZE / ID.
    message = mm.waveOutMessage
    message.argtypes = [W.HANDLE, W.UINT, C.c_size_t, C.c_size_t]
    device, flags, size = W.UINT(), W.DWORD(), W.DWORD()
    if message(WAVE_MAPPER, 0x2015, C.addressof(device), C.addressof(flags)):
        return WAVE_MAPPER, None
    if message(device.value, 0x812, C.addressof(size), 0) or not 2 <= size.value <= 65536:
        return device.value, None
    name = C.create_unicode_buffer((size.value+1)//2)
    if message(device.value, 0x811, C.addressof(name), size.value):
        return device.value, None
    return device.value, name.value
