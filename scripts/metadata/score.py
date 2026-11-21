from pathlib import Path
import struct,json,re,collections
from xml.etree import ElementTree as E
from collections import Counter,defaultdict
b=Path('work/registry.bin').read_bytes();n=struct.unpack_from('<I',b,24)[0];h=40+n*8;p=h+2*n
names={}
def fnv(s):
 v=2166136261
 for c in s.lower().encode():v=(v*16777619 & 0xffffffff)^c
 return v
for (hdr,) in struct.iter_unpack('>H',b[h:p]):
 w=hdr>>15;l=(hdr&32767)*(w+1);name=b[p:p+l].decode('utf-16-le' if w else 'utf8',errors='replace');p+=l
 if name.startswith('/') or len(name)>180:continue
 if 'bgm' in name.lower() or name.startswith(('AKE_', 'AKS_','AKSW_','AKP_','AKST_')):
  for v in (name, name.split('_',1)[-1]):names[fnv(v)]=v
r=E.fromstring('<all>'+Path('work/music.xml').read_text()+'</all>')
def ff(e,k):return [f.get('value') for f in e.findall(f".//field[@name='{k}']")]
def f(e,k,d='0'):return next(iter(ff(e,k)),d)
objs={int(f(o,'ulID')):o for o in r.findall('.//list[@name="listElement"]/object') if f(o,'ulID')!='0'}
if not objs:objs={int(f(o,'ulID')):o for o in r.iter('object') if o.find("field[@name='ulID']") is not None}
if __name__=='__main__':
 print('objs',len(objs),Counter(o.get('name') for o in objs.values()))
 ev=[(id,names.get(id)) for id,o in objs.items() if o.get('name')=='CAkEvent'];print('named events',sum(bool(name) for _,name in ev),ev[:12],[(i,n) for i,n in ev if n][:28])
 tops=Counter()
 for id,o in objs.items():
  if o.get('name')!='CAkMusicSegment':continue
  pid=id;path=[]
  while pid in objs:
   path.append((pid,objs[pid].get('name')));pid=int(f(objs[pid],'DirectParentID'))
  tops[path[-1]]+=1
 print('roots',tops)
 for c in ['CAkActionPlay','CAkMusicSwitchCntr','CAkMusicTrack']:
  o=next(o for o in objs.values() if o.get('name')==c)
  print(c,[(e.tag,e.get('name')) for e in o.iter() if e.tag in ('object','list')][:65])
 print('by registry',[(n) for n in names.values() if re.match(r'AKE_Bgm_M[sS]',n)][:35])
