"""Locate the installed game and build a verified, per-user audio cache."""
import ctypes
from ctypes import wintypes
import hashlib
import json
import msvcrt
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import threading
import winreg

from import_cockpit import render_cue

BLOCK=65536


def pak_directory(path,archives):
    if not str(path).strip():return None
    root=Path(str(path).strip().strip('"')).expanduser()
    for folder in (root/'Game/Content/Paks',root/'Content/Paks',root/'Paks',root):
        if all((folder/name).is_file() for name in archives):return folder
    return None


def find_game(archives):
    roots=[]
    for hive,key,field in [(winreg.HKEY_CURRENT_USER,r'Software\Valve\Steam','SteamPath'),
                           (winreg.HKEY_LOCAL_MACHINE,r'SOFTWARE\WOW6432Node\Valve\Steam','InstallPath')]:
        try:
            with winreg.OpenKey(hive,key) as k:roots.append(Path(winreg.QueryValueEx(k,field)[0]))
        except OSError:pass
    roots.append(Path(os.environ.get('ProgramFiles(x86)',r'C:\Program Files (x86)'))/'Steam')
    libraries=list(roots)
    for root in set(roots):
        vdf=root/'steamapps/libraryfolders.vdf'
        if vdf.is_file():
            libraries.extend(Path(v.replace('\\\\','\\')) for v in re.findall(
                r'"path"\s*"([^"]+)"',vdf.read_text(encoding='utf-8-sig',errors='replace')))
    for library in dict.fromkeys(libraries):
        apps=library/'steamapps'
        acf=apps/'appmanifest_2288340.acf'
        name='ACE COMBAT 8'
        if acf.is_file():
            match=re.search(r'"installdir"\s*"([^"]+)"',acf.read_text(encoding='utf8',errors='replace'))
            if match:name=match[1]
        game=apps/'common'/name
        if pak_directory(game,archives):return str(game)
    return ''


def choose_folder():
    shell=ctypes.windll.shell32;ole=ctypes.windll.ole32
    class BrowseInfo(ctypes.Structure):
        _fields_=[('hwndOwner',wintypes.HWND),('pidlRoot',ctypes.c_void_p),
                  ('pszDisplayName',wintypes.LPWSTR),('lpszTitle',wintypes.LPCWSTR),
                  ('ulFlags',wintypes.UINT),('lpfn',ctypes.c_void_p),
                  ('lParam',wintypes.LPARAM),('iImage',ctypes.c_int)]
    shell.SHBrowseForFolderW.argtypes=[ctypes.POINTER(BrowseInfo)]
    shell.SHBrowseForFolderW.restype=ctypes.c_void_p
    shell.SHGetPathFromIDListW.argtypes=[ctypes.c_void_p,wintypes.LPWSTR]
    ole.CoTaskMemFree.argtypes=[ctypes.c_void_p]
    display=ctypes.create_unicode_buffer(260);path=ctypes.create_unicode_buffer(260)
    ctypes.windll.user32.GetForegroundWindow.restype=wintypes.HWND
    info=BrowseInfo(ctypes.windll.user32.GetForegroundWindow(),None,ctypes.cast(display,wintypes.LPWSTR),
                    '选择 ACE COMBAT 8 游戏安装目录',0x41,None,0,0)
    hr=ole.OleInitialize(None)
    try:
        item=shell.SHBrowseForFolderW(ctypes.byref(info))
        if not item:return ''
        try:return path.value if shell.SHGetPathFromIDListW(item,path) else ''
        finally:ole.CoTaskMemFree(item)
    finally:
        if hr>=0:ole.OleUninitialize()


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def extract(source,entry,dest,build,codec):
    lengths=entry['blocks']
    with (source/entry['pak']).open('rb') as f:
        f.seek(entry['at']);packed=f.read(sum(lengths))
    if hashlib.sha256(packed).hexdigest()!=entry['packed']:
        raise ValueError(f"游戏资源与当前支持的 Steam 构建 {build} 不匹配，或游戏文件不完整。")
    cursor=0;remaining=entry['size'];sha=hashlib.sha256()
    with dest.open('wb') as f:
        for index,length in enumerate(lengths):
            size=entry['last'] if index==len(lengths)-1 else BLOCK
            codec.stdin.write(struct.pack('<II',length,size))
            codec.stdin.write(packed[cursor:cursor+length]);codec.stdin.flush();cursor+=length
            block=codec.stdout.read(size)
            if len(block)!=size:raise ValueError('资源块解压失败，请检查播放器是否已完整解压。')
            if index==0:block=block[entry['trim']:]
            block=block[:remaining];f.write(block);sha.update(block);remaining-=len(block)
    if remaining or sha.hexdigest()!=entry['sha256']:raise ValueError('游戏音源校验失败，请检查游戏文件完整性。')


class LibraryImport:
    def __init__(self,base,cache_root=None):
        self.base=base
        raw=(base/'library/import.json').read_bytes()
        self.version=hashlib.sha256(raw).hexdigest()
        self.spec=json.loads(raw);m=self.spec['music'];e=self.spec['effects'];c=self.spec['cues']
        self.archives={v['pak'] for v in (*m.values(),*e.values())}
        self.cache=(cache_root or Path(os.environ['LOCALAPPDATA'])/'AC8MusicPlayer/audio')/self.version[:16]
        self.files={Path('wem')/(k+'.wem'):v['size'] for k,v in m.items()}
        self.files.update({Path('cockpit')/(k+'.wav'):v for k,v in c.items()})
        self.path=None;self.thread=None;self.cancel=threading.Event();self.lock=threading.RLock()
        self.state=dict(phase='missing',game='',message='',done=0,total=len(m)+len(c),bytes=sum(self.files.values()),
                        build=self.spec['build'],cache=str(self.cache))
        legacy=base/'library'
        if self.complete(legacy):self.path=legacy
        elif self.published():self.path=self.cache
        if self.path:self.update(phase='ready',cache=str(self.path))
        else:self.update(game=find_game(self.archives))

    def complete(self,directory):
        try:return all((directory/p).stat().st_size==n for p,n in self.files.items())
        except OSError:return False

    def published(self):
        try:
            receipt=json.loads((self.cache/'complete.json').read_bytes())
            return receipt['version']==self.version and self.complete(self.cache)
        except (OSError,ValueError,KeyError):return False

    def update(self,**values):
        with self.lock:self.state.update(values)

    def status(self):
        with self.lock:return dict(self.state)

    def start(self,game):
        with self.lock:
            if self.path or self.thread and self.thread.is_alive():return self.status()
            if self.published():
                self.path=self.cache;self.update(phase='ready');return self.status()
            source=pak_directory(game,self.archives)
            if not source:
                self.update(phase='error',message='没有找到所需资源包，请选择 ACE COMBAT 8 的安装目录。')
                return self.status()
            self.update(phase='running',game=str(game),done=0,message='正在核对游戏音频')
            self.thread=threading.Thread(target=self.run,args=(source,),daemon=True)
            self.thread.start();return self.status()

    def run(self,source):
        try:
            self.cache.mkdir(parents=True,exist_ok=True)
            with (self.cache/'import.lock').open('a+b') as lock:
                lock.seek(0)
                try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                except OSError:raise ValueError('另一个播放器正在导入音频，完成后可在此重试。')
                try:
                    if not self.published():self.populate(source)
                finally:
                    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
            self.path=self.cache;self.update(phase='ready',message='')
        except Exception as exc:self.update(phase='error',message=str(exc))

    def populate(self,source):
        with subprocess.Popen([sys.executable,'-B',self.base/'decoder/unpack_blocks.py'],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as codec:
            try:self.extract_library(source,codec)
            finally:codec.stdin.close()

    def extract_library(self,source,codec):
        music=self.spec['music'];cues=self.spec['cues']
        missing=sum(n for p,n in self.files.items() if not (self.cache/p).is_file())
        if shutil.disk_usage(self.cache).free<missing+96*1024**2:
            raise ValueError(f'本机缓存空间不足，约需 {(missing+96*1024**2)/1024**3:.1f} GiB。')
        (self.cache/'wem').mkdir(exist_ok=True);(self.cache/'cockpit').mkdir(exist_ok=True)
        def check():
            if self.cancel.is_set():raise InterruptedError('导入已中断，下次会复用已校验的音源。')
        for i,(key,entry) in enumerate(music.items()):
            check();self.update(done=i,message=f'正在导入 BGM　{i+1} / {len(music)}')
            path=self.cache/'wem'/(key+'.wem')
            if path.is_file() and path.stat().st_size==entry['size'] and digest(path)==entry['sha256']:continue
            temp=path.with_suffix('.part')
            extract(source,entry,temp,self.spec['build'],codec);temp.replace(path)
        self.update(done=len(music),message='正在读取座舱音效')
        with tempfile.TemporaryDirectory(dir=self.cache,prefix='se-') as tmp:
            scratch=Path(tmp)
            for key,entry in self.spec['effects'].items():
                check();extract(source,entry,scratch/(key+'.wem'),self.spec['build'],codec)
            for i,(name,size) in enumerate(cues.items()):
                check();self.update(done=len(music)+i,message=f'正在生成 SE　{i+1} / {len(cues)}')
                dest=self.cache/'cockpit'/(name+'.wav')
                temp=dest.with_suffix('.part')
                render_cue(name,self.base/'decoder/vgmstream-cli.exe',scratch,temp)
                if temp.stat().st_size!=size:raise ValueError('生成的音效长度不匹配：'+name)
                temp.replace(dest)
        check()
        if not self.complete(self.cache):raise ValueError('音频库不完整，请重试导入。')
        receipt=self.cache/'complete.tmp'
        receipt.write_text(json.dumps(dict(version=self.version,game=str(source))),encoding='utf8')
        receipt.replace(self.cache/'complete.json')
        self.update(done=self.state['total'])

    def close(self):
        self.cancel.set()
        if self.thread:self.thread.join()
