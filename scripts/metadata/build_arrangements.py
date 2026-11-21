"""Build offline defaults from the extracted Wwise Play/SetState events and playlists."""
import collections
import json
import re
from pathlib import Path
from score import objs, f, ff, fnv, names

library = Path('library')
score = json.loads((library/'score.json').read_bytes())
labels = {int(k):v for k,v in json.loads(Path('work/state-labels.json').read_bytes()).items()}
reference_file=Path('work/arrangement-reference.json')
recordings=json.loads(reference_file.read_bytes()) if reference_file.exists() else {}

def nodes(obj, name):
    return obj.findall(f".//object[@name='{name}']")

def integer(obj, name, default=0):
    return int(f(obj, name, str(default)))

# A switch table lists possible destinations. Check the actions that select a
# state before treating it as a normal phase of playback.
stop_names={eid:name for eid,name in names.items() if name.startswith('AKE_Bgm_') and name.endswith('_Stop')}
for song in score['songs']:
    if song['event'].endswith('_Play'):
        prefix=song['event'].removesuffix('_Play')
        for stem in (prefix,prefix.split('_IGC_')[0]):
            name=stem+'_Stop'
            stop_names[fnv(name)]=name
state_writers=collections.defaultdict(set)
for eid,event in objs.items():
    if event.get('name')!='CAkEvent':continue
    for aid in ff(event,'ulActionID'):
        action=objs[int(aid)]
        if action.get('name')=='CAkActionSetState':
            state_writers[integer(action,'ulStateGroupID'),integer(action,'ulTargetStateID')].add(eid)
stop_states={state:sorted(stop_names[eid] for eid in writers) for state,writers in state_writers.items()
             if writers.issubset(stop_names)}

def lane_mask(sid, states):
    keys = []
    for lane in score['segments'][str(sid)]['lanes']:
        track = objs[lane['id']]
        volumes = 0
        for group in nodes(track, 'AkStateGroupChunk'):
            target = states.get(integer(group, 'ulStateGroupID'))
            for state in nodes(group, 'AkState'):
                if integer(state, 'ulStateID') == target:
                    volumes += sum(float(f(p,'pValue')) for p in nodes(state,'AkPropBundle') if integer(p,'pID')==0)
        switches = list(map(int,ff(track,'ulSwitchAssoc')))
        index = lane['variant']
        selected = states.get(integer(track,'uGroupID'),integer(track,'uDefaultSwitch'))
        if volumes > -96 and (not switches or index is not None and index<len(switches) and switches[index]==selected):
            keys.append(lane['key'])
    return keys

def state_rank(label):
    label=label.lower()
    suffix=re.sub(r'^.*?ms\d\d','',label)
    return ('end' in suffix, 'end' in suffix and 'preend' not in suffix,
            tuple((0,int(p)) if p.isdigit() else (1,p) for p in re.findall(r'\d+|[^\d_]+',suffix)))

def build(song):
    event_id=int(song['event']) if song['event'].isdigit() else fnv(song['event'])
    states={}
    if event_id in objs:
        for action in ff(objs[event_id],'ulActionID'):
            obj=objs[int(action)]
            if obj.get('name')=='CAkActionSetState':
                states[integer(obj,'ulStateGroupID')]=integer(obj,'ulTargetStateID')
    sources=set()
    stop_events=set()
    anchors=recordings.get(str(song['id']),{}).get('anchors',{})
    def route(node, st):
        obj=objs[node]
        if obj.get('name')=='CAkMusicSegment':
            return [dict(segment=node,repeat=1,lanes=lane_mask(node,st),changes=[])], None
        if obj.get('name')=='CAkMusicRanSeqCntr':
            def walk(item):
                sid=integer(item,'SegmentID')
                count=integer(item,'Loop',1)
                if sid:
                    row,_=route(sid,st)
                    row[0]['repeat']=count
                    return row,None
                rows=[]; loop=None
                for child in item.findall('list/object'):
                    items, tail=walk(child)
                    loop=len(rows)+tail if tail is not None else None
                    rows.extend(items)
                return rows*max(1,count), 0 if count==0 else loop
            return walk(nodes(obj,'AkMusicRanSeqPlaylistItem')[0])
        mapping={integer(n,'key'):integer(n,'audioNodeId') for n in nodes(obj,'Node')
                 if n.find("field[@name='audioNodeId']") is not None}
        if any(v and v not in objs for v in mapping.values()):
            sources.add('partial')
            mapping={k:v for k,v in mapping.items() if not v or v in objs}
        group=integer(obj,'ulGroup')
        initial=st.get(group)
        stop_keys=[k for k in mapping if k!=initial and (group,k) in stop_states]
        for k in stop_keys:
            stop_events.update(stop_states[group,k])
            del mapping[k]
        if stop_keys and not mapping:
            return [],None
        order=sorted((k for k,v in mapping.items() if k in labels and v and k not in (0,fnv('None'))),
                     key=lambda k:state_rank(labels[k]))
        first=initial if initial in mapping else next((k for k in (0,fnv('None')) if mapping.get(k)),None)
        if first is None and order:first=order[0]
        if first is None:first=next((k for k,v in mapping.items() if v),None)
        if first is None:
            children=list(map(int,ff(obj,'ulChildID')))
            assert len(children)==1,(node,children)
            return route(children[0],st)
        keys=[first,*[k for k in order if k!=first]]
        # Place otherwise unidentified branches only if the game gallery contains
        # distinctive audio for that branch. A reused clip is not a second cue.
        def members(k):
            return [sid for sid in song['order'] if mapping[k] in score['segments'][str(sid)]['anc']]
        def media(k):
            return {c['src'] for sid in members(k) for l in score['segments'][str(sid)]['lanes'] for c in l['clips']}
        def offset(k):
            for sid in members(k):
                times=anchors.get(str(sid),[])
                if times:return min(times)
            return float('inf')
        candidates={v:k for k,v in mapping.items() if v and v not in {mapping[n] for n in keys}}
        known=set().union(*(media(k) for k in keys))
        additions=[]
        for k in sorted(candidates.values(),key=offset):
            if offset(k)==float('inf') or not media(k)-known:continue
            # Two alternative edits made from the same recording cannot be
            # distinguished by their match time alone.
            if any(other!=k and abs(offset(other)-offset(k))<.4 and media(other)==media(k)
                   for other in candidates.values()):continue
            additions.append(k);known.update(media(k))
        for k in additions:
            at=next((i for i,n in enumerate(keys) if i and
                    ((offset(n)!=float('inf') and offset(n)>offset(k)) or 'end' in labels.get(n,'').lower())),len(keys))
            keys.insert(at,k);sources.add('recording')
        # A bank with one start branch and exactly one playable alternative
        # has an unambiguous order of first visits, although the trigger is unknown.
        if len(set(mapping[k] for k in keys))==1 and len(candidates)==1:
            alternative=next(iter(candidates.values()))
            if members(alternative):
                keys.append(alternative);sources.add('partial')
        if len({v for v in mapping.values() if v})>1:
            sources.add('states')
            if {v for v in mapping.values() if v} - {mapping[k] for k in keys}:sources.add('partial')
        rows=[]; loop=None; previous=None
        for k in keys:
            child=mapping[k]
            if not child:continue
            items,tail=route(child,{**st,group:k})
            if not items:continue
            if previous==child and rows:
                if rows[-1]['lanes']==items[-1]['lanes']:continue
                # A new state inside the same playlist leaves the intro behind.
                items=items[-1:];tail=None
            loop=len(rows)+tail if tail is not None else None
            rows.extend(items);previous=child
        return rows,loop
    rows,tail=route(song['id'],states)
    assert rows and all(r['segment'] in song['order'] for r in rows)
    for row in rows[:-1]:row['repeat']=max(1,row['repeat'])
    if tail is not None and tail==len(rows)-1: rows[-1]['repeat']=0;tail=None
    if rows[-1]['repeat']==0:tail=None
    source='recording' if 'recording' in sources else 'partial' if 'partial' in sources else 'states' if 'states' in sources else 'playlist'
    plan=dict(id='default',steps=rows,loopFrom=tail,source=source)
    if stop_events:plan['stopEvents']=sorted(stop_events)
    return plan

plans={s['id']:build(s) for s in score['songs'] if s['bank']=='互动配乐'}
(library/'arrangements.json').write_text(json.dumps(plans,ensure_ascii=False,separators=(',',':')),encoding='utf8')
print(collections.Counter(p['source'] for p in plans.values()), 'terminal groups',sum(p['loopFrom'] is not None for p in plans.values()))
