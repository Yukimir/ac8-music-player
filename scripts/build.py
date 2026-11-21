"""Build the Windows x64 player and its offline, game-audio-free distribution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request
from zipfile import ZipFile, ZIP_DEFLATED

try:
    import tomllib
except ImportError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]
APP = ('arrangement','bridge','cockpit','device','engine','game_import','import_cockpit','spectrum')
METADATA = ('arrangements.json','import.json','score.json')
GAME_MEDIA = {'.pak','.utoc','.ucas','.wem','.bnk','.wav','.ogg','.mp3','.flac'}


def dependencies(cache,offline):
    cache.mkdir(parents=True,exist_ok=True)
    artifacts={}
    for key,dep in json.loads((ROOT/'scripts/dependencies.json').read_bytes()).items():
        path=cache/dep['url'].rsplit('/',1)[-1]
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=dep['sha256']:
            if offline:raise SystemExit(f'Missing or invalid cached dependency: {path.name}')
            print('Downloading',path.name,flush=True)
            req=urllib.request.Request(dep['url'],headers={'User-Agent':'AC8-Music-Player-build'})
            with tempfile.NamedTemporaryFile(dir=cache,delete=False) as tmp:
                partial=Path(tmp.name)
            try:
                with urllib.request.urlopen(req,timeout=90) as response, partial.open('wb') as out:
                    shutil.copyfileobj(response,out)
                if hashlib.sha256(partial.read_bytes()).hexdigest()!=dep['sha256']:
                    raise SystemExit(f'Checksum mismatch: {path.name}')
                partial.replace(path)
            finally:partial.unlink(missing_ok=True)
        artifacts[key]=path
    return artifacts


def rust_notices(dest):
    cargo=Path(os.environ.get('CARGO_HOME',Path.home()/'.cargo'))
    packages=tomllib.loads((ROOT/'desktop/Cargo.lock').read_text(encoding='utf8'))['package']
    with ZipFile(dest,'w',ZIP_DEFLATED,strict_timestamps=False) as out:
        for pkg in packages:
            name=pkg['name']+'-'+pkg['version']
            crate=next((cargo/'registry/src').glob('*/'+name),None)
            if crate:
                for file in sorted(crate.iterdir()):
                    if file.is_file() and file.name.lower().startswith(('license','copying','notice','copyright')):
                        out.write(file,name+'/'+file.name)


def stage_player(stage,exe,deps):
    (stage/'app').mkdir();(stage/'library').mkdir();(stage/'decoder').mkdir()
    shutil.copyfile(exe,stage/'皇牌空战8音乐播放器.exe')
    for name in APP:shutil.copyfile(ROOT/'app'/(name+'.py'),stage/'app'/(name+'.py'))
    for name in METADATA:shutil.copyfile(ROOT/'library'/name,stage/'library'/name)
    shutil.copyfile(ROOT/'decoder/unpack_blocks.py',stage/'decoder/unpack_blocks.py')
    with ZipFile(deps['vgmstream']) as z:
        for n in z.namelist():
            if n.endswith('.dll') or n in ('vgmstream-cli.exe','COPYING'):z.extract(n,stage/'decoder')
    with ZipFile(deps['pyooz']) as z:z.extract('ooz.pyd',stage/'decoder')
    with ZipFile(deps['python']) as z:z.extractall(stage/'runtime')
    with ZipFile(deps['numpy']) as z:
        for n in z.namelist():
            if not {'tests','__pycache__'}.intersection(Path(n).parts):z.extract(n,stage/'runtime/packages')
    (stage/'runtime/python313._pth').write_text('python313.zip\n.\npackages\n../app\n',encoding='ascii')
    shutil.copytree(ROOT/'notices',stage/'licenses')
    shutil.copyfile(deps['pyooz_source'],stage/'licenses'/deps['pyooz_source'].name)
    rust_notices(stage/'licenses/desktop-notices.zip')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache-dir',type=Path,default=ROOT/'build/downloads')
    parser.add_argument('--offline',action='store_true',help='Use cached downloads and Cargo crates only')
    options=parser.parse_args()
    if os.name!='nt':raise SystemExit('Use Windows x64 with the Rust MSVC toolchain.')
    build=ROOT/'build';build.mkdir(exist_ok=True)
    deps=dependencies(options.cache_dir.resolve(),options.offline)
    target=Path(os.environ.get('CARGO_TARGET_DIR',build/'target')).resolve()
    cmd=['cargo','build','--release','--locked','--manifest-path',str(ROOT/'desktop/Cargo.toml'),'--target-dir',str(target)]
    if options.offline:cmd.append('--offline')
    subprocess.run(cmd,cwd=ROOT,check=True)
    dist=ROOT/'dist';dist.mkdir(exist_ok=True)
    archive=dist/'AC8音乐播放器-无游戏音频-Windows-x64.zip'
    with tempfile.TemporaryDirectory(dir=build) as tmp:
        stage=Path(tmp)/'AC8 音乐播放器';stage.mkdir()
        stage_player(stage,target/'release/ac8-music-player.exe',deps)
        output=Path(tmp)/archive.name
        with ZipFile(output,'w',ZIP_DEFLATED,compresslevel=6) as z:
            for file in sorted(stage.rglob('*')):
                if file.is_file():
                    if file.suffix.lower() in GAME_MEDIA:raise ValueError(f'Unexpected game media: {file.name}')
                    z.write(file,file.relative_to(stage.parent))
        with ZipFile(output) as z:
            if bad:=z.testzip():raise ValueError(f'Damaged archive member: {bad}')
        output.replace(archive)
    print(archive)


if __name__=='__main__':main()
