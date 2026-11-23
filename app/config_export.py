"""Save global player settings using the Windows common file dialog."""
import ctypes
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import tempfile


class OpenFileName(ctypes.Structure):
    _fields_ = [('size',W.DWORD),('owner',W.HWND),('instance',W.HINSTANCE),
                ('filter',W.LPCWSTR),('customFilter',W.LPWSTR),('maxCustom',W.DWORD),
                ('filterIndex',W.DWORD),('file',W.LPWSTR),('maxFile',W.DWORD),
                ('fileTitle',W.LPWSTR),('maxTitle',W.DWORD),('initialDir',W.LPCWSTR),
                ('title',W.LPCWSTR),('flags',W.DWORD),('fileOffset',W.WORD),
                ('extensionOffset',W.WORD),('defaultExt',W.LPCWSTR),('customData',ctypes.c_ssize_t),
                ('hook',ctypes.c_void_p),('template',W.LPCWSTR),('reserved',ctypes.c_void_p),
                ('reservedCount',W.DWORD),('extraFlags',W.DWORD)]


def save_backup(data):
    if not isinstance(data,dict) or data.get('format')!='ac8-library-settings': raise ValueError('备份格式无效')
    buf=ctypes.create_unicode_buffer('AC8 播放器配置.json',32768)
    opts=OpenFileName();opts.size=ctypes.sizeof(opts)
    ctypes.windll.user32.GetForegroundWindow.restype=W.HWND
    opts.owner=ctypes.windll.user32.GetForegroundWindow()
    opts.file=ctypes.cast(buf,W.LPWSTR);opts.maxFile=len(buf)
    opts.filter='JSON (*.json)\0*.json\0\0';opts.defaultExt='json'
    opts.title='导出全部编排和播放列表';opts.flags=0x80000|0x800|0x8|0x2
    if not ctypes.windll.comdlg32.GetSaveFileNameW(ctypes.byref(opts)):
        if ctypes.windll.comdlg32.CommDlgExtendedError():raise OSError('无法打开保存对话框')
        return False
    dest=Path(buf.value)
    with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as f:temp=Path(f.name)
    try:
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
        os.replace(temp,dest)
    finally:temp.unlink(missing_ok=True)
    return True
