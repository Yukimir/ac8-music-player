"""Locate the installed game and build a verified audio library beside the player."""
import ctypes
from ctypes import wintypes
import hashlib
import json
from contextlib import contextmanager
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

from import_cockpit import ENGINES, MIXES, render_cue

BLOCK=65536


def pak_directory(path,archives):
    if not str(path).strip():return None
    root=Path(str(path).strip().strip('"')).expanduser()
    for folder in (root/'Game/Content/Paks',root/'Content/Paks',root/'Paks',root):
        if any((folder/name).is_file() for name in archives):return folder
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
        self.spec=json.loads(raw)
        self.native=json.loads((base/'library/native.json').read_bytes())
        self.archives={entry['pak'] for entry in self.spec['music'].values()}
        self.cache=(cache_root or base/'audio')/self.version[:16]
        self.roots=(self.cache,base/'library')
        self.config_file=self.cache.parent/'settings.json'
        self.verified={}
        self.lock=threading.RLock()
        self.cancel=threading.Event()
        self.thread=None
        self.revision=0
        try:
            config=json.loads(self.config_file.read_bytes())
            game=config['game']
        except (OSError,KeyError,ValueError):
            config={}
            try:previous_game=json.loads((self.cache/'complete.json').read_bytes())['game']
            except (OSError,KeyError,ValueError):previous_game=''
            game=previous_game if pak_directory(previous_game,self.archives) else find_game(self.archives)
        self.source=pak_directory(game,self.archives)
        self.config=dict(game=game,extract=config.get('extract',False),native=config.get('native',False))
        ready=self.has_cache() or bool(config and self.source)
        self.path=self.cache if ready else None
        self.state=dict(phase='ready' if ready else 'missing',message='',done=0,total=0,
                        bytes=sum(e['size'] for e in self.spec['music'].values())+sum(self.spec['cues'].values()),
                        nativeBytes=sum(e['size'] for e in self.native.values()),cache=str(self.cache))

    def has_cache(self):
        return any(next((root/folder).glob(pattern),None) for root in self.roots
                   for folder,pattern in [('wem','*.wem'),('native','*.wem'),('cockpit','*.wav')])

    def status(self):
        with self.lock:return {**self.config,**self.state,'configured':self.path is not None}

    def update(self,**values):
        with self.lock:self.state.update(values)

    def cached(self,relative,size,sha=None):
        for root in self.roots:
            path=root/relative
            try:
                stat=path.stat()
                if stat.st_size!=size:continue
                stamp=(stat.st_size,stat.st_mtime_ns)
                if sha and self.verified.get(path)!=stamp:
                    if digest(path)!=sha:continue
                    self.verified[path]=stamp
                return path
            except FileNotFoundError:continue
        return None

    @contextmanager
    def codec(self):
        with subprocess.Popen([sys.executable,'-B',self.base/'decoder/unpack_blocks.py'],
                              stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)) as proc:
            try:yield proc
            finally:proc.stdin.close()

    def unpack(self,source,entry,dest,codec,label):
        if source is None or not (source/entry['pak']).is_file():
            raise ValueError(f'缺少音频 {label}。请在右上角设置里选择游戏目录，或放回已提取的素材。')
        extract(source,entry,dest,self.spec['build'],codec)

    @contextmanager
    def music(self,key,scratch):
        native=key.startswith('native-')
        name=key.removeprefix('native-') if native else key
        entry=(self.native if native else self.spec['music'])[name]
        relative=Path('native' if native else 'wem')/(name+'.wem')
        cached=self.cached(relative,entry['size'],entry['sha256'])
        if cached:
            yield cached
            return
        temp=scratch/(key+'.wem')
        try:
            with self.codec() as codec:self.unpack(self.source,entry,temp,codec,name)
            yield temp
        finally:temp.unlink(missing_ok=True)

    def cue(self,name,scratch,source,codec):
        size=self.spec['cues'][name]
        relative=Path('cockpit')/(name+'.wav')
        cached=self.cached(relative,size)
        if cached:return cached
        keys=[ENGINES[name]] if name in ENGINES else [layer[0] for layer in MIXES[name]]
        for key in keys:
            dest=scratch/(str(key)+'.wem')
            if not dest.exists():self.unpack(source,self.spec['effects'][str(key)],dest,codec,name)
        dest=scratch/(name+'.wav')
        render_cue(name,self.base/'decoder/vgmstream-cli.exe',scratch,dest)
        if dest.stat().st_size!=size:raise ValueError('音效校验失败：'+name)
        return dest

    @contextmanager
    def cockpit(self):
        with tempfile.TemporaryDirectory(prefix='AC8-SE-') as tmp,self.codec() as codec:
            scratch=Path(tmp)
            yield lambda name:self.cue(name,scratch,self.source,codec)

    def commit(self,config,source):
        self.config_file.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=self.config_file.parent,delete=False) as tmp:
            path=Path(tmp.name)
        try:
            path.write_text(json.dumps(config,ensure_ascii=False),encoding='utf8')
            path.replace(self.config_file)
        finally:path.unlink(missing_ok=True)
        with self.lock:
            self.config=config
            self.source=source
            self.path=self.cache
            self.revision+=1
            self.update(phase='ready',message='',done=self.state['total'])

    def start(self,game,persist=False,native=False):
        with self.lock:
            if self.thread and self.thread.is_alive():return self.status()
            self.cancel.clear()
            game=game.strip()
            source=pak_directory(game,self.archives)
            config=dict(game=game,extract=bool(persist),native=bool(persist and native))
            if source is None and (not self.has_cache() or (game and game!=self.config['game'])):
                self.update(phase='error',message='没有找到游戏资源包。请选择游戏目录，或使用已提取的音频。')
            elif persist:
                self.update(phase='running',done=0,total=len(self.spec['music'])+len(self.spec['cues'])+
                            (len(self.native) if native else 0),message='正在检查已提取的素材')
                self.thread=threading.Thread(target=self.run,args=(config,source),daemon=True)
                self.thread.start()
            else:self.commit(config,source)
            return self.status()

    def run(self,config,source):
        try:
            self.cache.mkdir(parents=True,exist_ok=True)
            with (self.cache/'import.lock').open('a+b') as lock:
                lock.seek(0)
                try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                except OSError:raise ValueError('另一个播放器正在提取音频，请稍后重试。')
                try:
                    self.populate(source,config['native'])
                    self.commit(config,source)
                finally:
                    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        except Exception as exc:self.update(phase='error',message=str(exc))

    def populate(self,source,include_native):
        groups=[('wem','BGM',self.spec['music'])]
        if include_native:groups.append(('native','原生 7.1.4',self.native))
        tasks=[]
        for folder,label,entries in groups:
            for name,entry in entries.items():
                tasks.append((Path(folder)/(name+'.wem'),label,entry['size'],entry))
        for name,size in self.spec['cues'].items():
            tasks.append((Path('cockpit')/(name+'.wav'),'座舱音效',size,None))
        missing=[];space=0
        for rel,label,size,entry in tasks:
            if self.cancel.is_set():raise InterruptedError('已中断提取')
            if not self.cached(rel,size,entry['sha256'] if entry else None):
                missing.append((rel,label,entry))
                space+=size
        if not missing:return
        if source is None:raise ValueError(f'还缺 {len(missing)} 个素材。请选择游戏目录后继续提取。')
        if shutil.disk_usage(self.cache).free<space+96*1024**2:
            raise ValueError(f'磁盘空间不足，还需约 {space/1024**3:.2f} GiB。')
        with tempfile.TemporaryDirectory(dir=self.cache,prefix='import-') as tmp,self.codec() as codec:
            scratch=Path(tmp)
            for i,(rel,label,entry) in enumerate(missing,len(tasks)-len(missing)):
                if self.cancel.is_set():raise InterruptedError('已中断提取')
                self.update(done=i,message=f'正在提取 {label}　{i+1} / {len(tasks)}')
                dest=self.cache/rel
                dest.parent.mkdir(exist_ok=True)
                if entry:
                    temp=scratch/'media.part'
                    self.unpack(source,entry,temp,codec,rel.stem)
                else:temp=self.cue(rel.stem,scratch,source,codec)
                if temp.parent==scratch:temp.replace(dest)

    def close(self):
        self.cancel.set()
        if self.thread:self.thread.join()
