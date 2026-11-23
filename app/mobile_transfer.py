"""A user-opened LAN session for transferring a selection from the installed library."""
import base64
import hashlib
import hmac
import ipaddress
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit

from arrangement import normalize
from game_import import digest
from qrcodegen import QrCode


def addresses():
    # DNS registration alone misses Wi-Fi, VPN and secondary Ethernet addresses.
    command = ("[Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces() | "
               "Where-Object OperationalStatus -eq Up | ForEach-Object { "
               "$_.GetIPProperties().UnicastAddresses | Where-Object { $_.Address.AddressFamily -eq 'InterNetwork' } | "
               "ForEach-Object { $_.Address.IPAddressToString } }")
    candidates = []
    try:
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
                                capture_output=True, timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        candidates.extend(result.stdout.decode("ascii", errors="ignore").splitlines())
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        candidates.extend(a[4][0] for a in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET))
    except OSError:
        pass
    hosts = []
    for item in candidates:
        try: addr = ipaddress.IPv4Address(item.strip())
        except ValueError: continue
        if addr.is_loopback or addr.is_multicast or addr.is_unspecified: continue
        if str(addr) not in hosts: hosts.append(str(addr))
    if not hosts: raise ValueError("没有可用的局域网 IPv4 地址，请检查 Wi-Fi 或有线网络。")
    return hosts


def qr_image(payload):
    qr = QrCode.encode_text(payload, QrCode.Ecc.MEDIUM)
    cells = " ".join(f"M{x},{y}h1v1h-1z" for y in range(qr.get_size())
                     for x in range(qr.get_size()) if qr.get_module(x, y))
    n = qr.get_size()+8
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-4 -4 {n} {n}" shape-rendering="crispEdges">'
           f'<rect x="-4" y="-4" width="{n}" height="{n}" fill="white"/><path d="{cells}"/></svg>')
    return "data:image/svg+xml;base64,"+base64.b64encode(svg.encode()).decode()


class Transfer:
    def __init__(self, base, resolver):
        self.base, self.resolver = base, resolver
        self.lock = threading.RLock()
        self.score = json.loads((base/'library/score.json').read_bytes())
        self.songs = {s['id']: s for s in self.score['songs']}
        self.defaults = json.loads((base/'library/arrangements.json').read_bytes())
        self.segments = {int(k):v for k,v in self.score['segments'].items()}
        self.server = None
        self.effect_version = hashlib.sha256((base/'app/import_cockpit.py').read_bytes()).hexdigest() + resolver.spec['build']

    def open(self):
        self.close()
        token = secrets.token_urlsafe(32)
        class Handler(TransferHandler): pass
        Handler.transfer = self
        Handler.secret = token
        server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
        try:
            hosts = addresses()
            payload = json.dumps(dict(ac8=1, ips=hosts, port=server.server_port, token=token),separators=(',',':'))
            payload = 'ac8pair:'+base64.urlsafe_b64encode(payload.encode()).decode().rstrip('=')
            image = qr_image(payload)
        except BaseException:
            server.server_close()
            raise
        with self.lock:
            self.server, self.secret = server, token
            self.device = None
            self.inventory = {}
            self.job = None
            self.message = '等待手机扫码'
            self.last_seen = 0
            self.downloads = 0
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return dict(image=image, code=payload, ips=hosts)

    def close(self):
        with self.lock:
            server, self.server = self.server, None
            self.secret = ''
            self.job = None
        if server:
            server.shutdown()
            server.server_close()

    def status(self):
        with self.lock:
            if not self.server: return dict(open=False)
            return dict(open=True, device=self.device, inventory=self.inventory, message=self.message,
                        connected=bool(self.device and (self.downloads or time.monotonic()-self.last_seen<15)),
                        busy=self.job is not None)

    def queue(self, request):
        selected = request.get('songs',[])
        if not isinstance(selected,list) or len(selected)>len(self.songs): raise ValueError('选曲无效')
        songs = {i:self.songs[i] for i in selected}
        raw_plans = request.get('plans',{})
        plans, parts, rules, files, refs = {}, {}, {}, {}, {}
        def resource(kind, key):
            if kind=='music':
                stamp=self.resolver.spec['music'][key]['sha256']
            else: stamp=self.effect_version+':'+key
            ident=hashlib.sha256(('pcm16-v1:'+stamp).encode()).hexdigest()
            files[ident]=(kind,key)
            return ident
        for i,s in songs.items():
            sid=str(i); included=set(s['order'])
            for seg in s['order']:
                for ancestor in self.segments[seg]['anc']:
                    rr=self.score['rules'].get(str(ancestor),[])
                    if rr: rules[str(ancestor)]=rr
                    for rule in rr:
                        bridge=rule.get('bridge',{}).get('id')
                        if bridge in self.segments: included.add(bridge)
            for seg in included:
                part=self.segments[seg];parts[str(seg)]=part
                for lane in part['lanes']:
                    for clip in lane['clips']:
                        key=clip['src']
                        if key not in refs: refs[key]=resource('music',key)
            plans[sid]={}
            if sid in self.defaults: plans[sid]['default']=self.defaults[sid]
            for p in raw_plans.get(sid,[]):
                norm=normalize(p,s,self.segments)
                if norm['id'] in ('free','default') or not isinstance(p.get('name'),str) or not p['name'].strip():
                    raise ValueError('自定义编排无效')
                plans[sid][norm['id']]={**norm,'name':p['name']}
        cues={}
        if request.get('effects'):
            cues={k:resource('cue',k) for k in self.resolver.spec['cues']}
        if not songs and not cues: raise ValueError('请选择曲目或音效包')
        manifest=dict(id=secrets.token_hex(12),songs={str(i):s for i,s in songs.items()},segments=parts,
                      rules=rules,plans=plans,media=refs,cues=cues)
        entries=request.get('playlist')
        if entries:
            if not isinstance(entries,list):raise ValueError('播放列表无效')
            seen=set()
            for entry in entries:
                key=entry['id']; sid=str(entry['songId']); plan=entry['planId']
                if (not isinstance(key,str) or not key or key in seen or sid not in plans or
                        (plan not in plans[sid] if plans[sid] else plan is not None)):
                    raise ValueError('播放列表条目或编排无效')
                seen.add(key)
            manifest['playlist']=entries
        with self.lock:
            if not self.server or not self.device or time.monotonic()-self.last_seen>15: raise ValueError('请先连接手机')
            if self.job: raise ValueError('上一次传输仍在进行')
            self.job=(manifest,files)
            self.message='等待手机接收'
        return self.status()

    def prepare_file(self, kind, key, scratch):
        if kind=='music':
            dest=scratch/'audio.wav'
            with self.resolver.music(key,scratch) as source:
                subprocess.run([self.base/'decoder/vgmstream-cli.exe','-i','-o',dest,source],
                               check=True,capture_output=True,creationflags=subprocess.CREATE_NO_WINDOW)
            return dest
        with self.resolver.codec() as codec:
            return self.resolver.cue(key,scratch,self.resolver.source,codec)


class TransferHandler(BaseHTTPRequestHandler):
    server_version='AC8'
    def log_message(self,*args): pass
    def setup(self):
        super().setup()
        self.connection.settimeout(45)

    def reply(self,obj,status=200):
        data=json.dumps(obj,ensure_ascii=False,separators=(',',':')).encode()
        self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)

    def handle_request(self, post):
        t=self.transfer
        path=urlsplit(self.path).path
        if not post and path=='/identify':
            nonce=self.headers.get('X-AC8-Challenge','')
            if not 16<=len(nonce)<=80 or not nonce.isascii() or t.secret!=self.secret:return self.reply({},400)
            proof=hmac.new(self.secret.encode(),nonce.encode(),hashlib.sha256).hexdigest()
            return self.reply({'proof':proof})
        with t.lock:
            if t.secret!=self.secret or not secrets.compare_digest(self.headers.get('Authorization',''),'Bearer '+self.secret):
                return self.reply({},403)
            dev=self.headers.get('X-AC8-Device','')
            if not dev or len(dev)>100 or (t.device and dev!=t.device['id']):return self.reply({},409)
        body={}
        if post:
            n=int(self.headers.get('Content-Length',0))
            if not 0<n<=2*1024*1024:return self.reply({},413)
            body=json.loads(self.rfile.read(n))
        with t.lock:
            if t.secret!=self.secret or t.device and dev!=t.device['id']:return self.reply({},409)
            if post and path=='/pair':
                if not isinstance(body.get('songs'),list) or not isinstance(body.get('plans',{}),dict):return self.reply({},400)
                t.device=dict(id=dev,name=str(body.get('name','Android'))[:70])
                t.inventory={k:body.get(k) for k in ('songs','plans','effects')}
                t.last_seen=time.monotonic()
                if not t.job:t.message='手机已连接'
                return self.reply({'ac8':1})
            if not t.device:return self.reply({},403)
            t.last_seen=time.monotonic()
            job=t.job
            if not post and path=='/offer':return self.reply(job[0] if job else {})
            if post and path=='/progress' and job and body.get('id')==job[0]['id']:
                t.message=str(body.get('message',''))[:200]
                if body.get('done') or body.get('failed'):t.job=None
                return self.reply({})
            if post or not job:return self.reply({},404)
            ident=path.split('/')
            if len(ident)!=4 or ident[1]!='asset' or ident[2]!=job[0]['id'] or ident[3] not in job[1]:
                return self.reply({},404)
            source=job[1][ident[3]]
            t.downloads+=1
        try:
            with tempfile.TemporaryDirectory(prefix='ac8-mobile-') as tmp:
                file=t.prepare_file(*source,Path(tmp))
                with file.open('rb') as f:
                    self.send_response(200);self.send_header('Content-Type','audio/wav')
                    self.send_header('Content-Length',str(file.stat().st_size))
                    self.send_header('X-Content-SHA256',digest(file));self.end_headers()
                    while block:=f.read(256*1024):
                        if t.secret!=self.secret or t.job is not job:return
                        self.wfile.write(block)
        finally:
            with t.lock:
                if t.secret==self.secret:
                    t.downloads-=1
                    t.last_seen=time.monotonic()

    def handle_safe(self,post):
        try:self.handle_request(post)
        except (BrokenPipeError,ConnectionResetError,TimeoutError):pass
        except Exception as exc:
            try:self.reply({'error':str(exc)},400)
            except OSError:pass
    def do_POST(self):self.handle_safe(True)
    def do_GET(self):self.handle_safe(False)
