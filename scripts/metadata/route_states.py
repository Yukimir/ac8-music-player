from score import objs,f,ff,fnv,names
import json,re,itertools,collections
from pathlib import Path
D=json.loads(Path('library/score.json').read_bytes())
S=[s for s in D['songs'] if s['bank']=='互动配乐']
def nodes(o,n):return o.findall(".//object[@name='"+n+"']")
def num(o,n):return int(f(o,n))
def branch_keys(o):
    return {num(n,'key'):num(n,'audioNodeId') for n in nodes(o,'Node') if n.find("field[@name='audioNodeId']") is not None}
def state_event(ev):
    return {num(objs[int(i)],'ulStateGroupID'):num(objs[int(i)],'ulTargetStateID') for i in ff(ev,'ulActionID') if objs[int(i)].get('name')=='CAkActionSetState'}
states=collections.defaultdict(set)
for s in S:
    k=s['id'];g=num(objs[k],'ulGroup')
    if g:states[g].update(branch_keys(objs[k]))
allkeys=set.union(*states.values())
for ev in objs.values():
    if ev.get('name')=='CAkEvent':allkeys.update(state_event(ev).values())
found={k:n for k,n in names.items() if k in allkeys}
base={s['event'].removeprefix('AKE_Bgm_').removesuffix('_Play').split('_IGC')[0] for s in S}
base.update(re.findall(r'(?:AKE_Bgm_)([A-Za-z0-9_]+?)_(?:Play|State)',Path('work/sfx-names.txt').read_text(),re.I))
nums=[str(n) for n in range(1,32)]+[str(n).zfill(2) for n in range(1,16)]
parts=set(['','Start','Intro','Loop','PreEnd','End','ED','A','B','C','D','E','F','Outro','Default','Normal','Battle','Main']+nums)
for token in ['Phase','Part','Sequence','Block','Step','Start','End','Loop','Layer','Intro','A','B','C','D','E','F']:
    for n in nums:parts.update((token+n,token+'_'+n,n+token))
for a,b in itertools.product('ABCDE0123456789',nums):
    parts.update((a+b,a+'_'+b))
for n in range(1,10):
    for a,b in itertools.product('ABCDE','0123456789'):
        parts.update((str(n)+a+b,str(n)+a+'_'+b,str(n)+'_'+a+b))
for b in tuple(base):
    m=re.match(r'(Ms\d\d)',b,re.I)
    if m:
        base.update([m[1],*(m[1]+a for a in 'ABCDE')])
for n in range(1,31):
    for a in 'ABCDE':
        parts.update([a+str(n).zfill(2),str(n)+a+'_End',str(n)+a+'End',a+'_'+str(n).zfill(2)])
    parts.update([str(n)+'_End',str(n)+'End','End'+str(n),'_'+str(n)])
for chapter,letter,n in itertools.product('1234','ABCDE',range(0,101,5)):
    parts.update([chapter+letter+str(n),chapter+letter+str(n).zfill(2),chapter+letter+'_'+str(n)])
# Some event names describe a phase even though its state uses another internal name.
inverse=pow(16777619,-1,2**32)
events={}
for eid,ev in objs.items():
    if ev.get('name')!='CAkEvent':continue
    by_group=collections.defaultdict(list)
    for act in ff(ev,'ulActionID'):
        a=objs[int(act)]
        if a.get('name')=='CAkActionSetState' and '58' not in ff(a,'pID'):
            by_group[num(a,'ulStateGroupID')].append(num(a,'ulTargetStateID'))
    for suffix in ('_State','_Play'):
        h=eid
        for c in suffix.lower().encode()[::-1]:h=((h^c)*inverse)&0xffffffff
        events[h]=[v[0] for v in by_group.values() if len(v)==1]
event_labels={}
for b in base:
    for sep in ('_',''):
        start=fnv(b+sep)
        event_start=fnv('AKE_Bgm_'+b+sep)
        for p in parts:
            v=start;e=event_start
            for c in p.lower().encode():
                v=(v*16777619&0xffffffff)^c;e=(e*16777619&0xffffffff)^c
            if v in allkeys:found[v]=b+sep+p
            name=b+sep+p
            if e in events and p:
                for value in events[e]:
                    if value in allkeys and len(name)>len(event_labels.get(value,'')):event_labels[value]=name
for k,name in names.items():
    if k in objs and objs[k].get('name')=='CAkEvent' and name.endswith(('_State','_Play')):
        for value in state_event(objs[k]).values():
            direct=name.removeprefix('AKE_Bgm_').rsplit('_',1)[0]
            if fnv(direct)==value: found[value]=direct
for k,label in event_labels.items():found.setdefault(k,label)
Path('work/state-labels.json').write_text(json.dumps(found,ensure_ascii=False),encoding='utf8')
for s in S:
    br=branch_keys(objs[s['id']])
    if br:print(s['event'],[(k,found.get(k),v) for k,v in br.items() if k])
