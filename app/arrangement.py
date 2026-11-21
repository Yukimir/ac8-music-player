"""Validate arrangement data before it reaches the audio thread."""
import re


def integer(value, low, high, label):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(label+'超出范围')
    return value


def normalize(plan, song, segments):
    if not isinstance(plan, dict) or not isinstance(plan.get('id'),str) or not re.fullmatch(r'[a-zA-Z0-9_-]{1,80}',plan['id']):
        raise ValueError('编排编号无效')
    raw = plan.get('steps')
    if not isinstance(raw,list) or not 1 <= len(raw) <= 512:
        raise ValueError('编排需要 1 至 512 个段落')
    steps = []
    for index, item in enumerate(raw):
        if not isinstance(item,dict) or item.get('segment') not in song['order']:
            raise ValueError('编排包含其他曲目的段落')
        seg = segments[item['segment']]
        repeat = integer(item.get('repeat',1),0 if index==len(raw)-1 else 1,999,'段落遍数')
        bars = max(1,(seg['exit']-seg['entry']+seg['bar']-1)//seg['bar']) if seg['bar'] else 1
        length=item.get('bars')
        if length is not None:bars=integer(length,1,bars,'每遍长度')
        lanes = {l['key']:l for l in seg['lanes']}
        def mask(keys):
            if not isinstance(keys,list) or any(not isinstance(k,str) or k not in lanes for k in keys):
                raise ValueError('编排音轨无效')
            selected=set(keys)
            variants=[lanes[k]['id'] for k in selected if lanes[k]['variant'] is not None]
            if len(variants)!=len(set(variants)):raise ValueError('同组分轨只能启用一个变体')
            return [k for k in lanes if k in selected]
        initial=mask(item.get('lanes',[k for k,lane in lanes.items() if lane['on']]))
        changes=item.get('changes',[])
        if not isinstance(changes,list) or len(changes)>256:raise ValueError('分轨节点过多')
        events={}
        for change in changes:
            if not isinstance(change,dict):raise ValueError('分轨节点无效')
            run=integer(change.get('run',1),1,repeat or 999,'分轨节点遍数')
            bar=integer(change.get('bar'),1,bars,'分轨节点小节')
            if (run,bar) in events:raise ValueError('同一遍、同一小节只能设置一次分轨')
            events[run,bar]=dict(run=run,bar=bar,lanes=mask(change.get('lanes')))
        steps.append(dict(segment=seg['id'],repeat=repeat,bars=length,lanes=initial,changes=[events[k] for k in sorted(events)]))
    tail=plan.get('loopFrom')
    if tail is not None:
        integer(tail,0,len(steps)-1,'末尾返回位置')
        if not steps[-1]['repeat']:raise ValueError('末段无限循环时不能再设置返回位置')
    return dict(id=plan['id'],steps=steps,loopFrom=tail)
