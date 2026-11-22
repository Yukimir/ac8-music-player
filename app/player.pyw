from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import ctypes
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox

import numpy as np
from engine import Media, Transport, RATE
from device import Output

BASE=Path(__file__).resolve().parent.parent
NAV='#13262a'; NAV2='#21393d'; PAPER='#f3f4ee'; CARD='#fcfcf7'
INK='#152b2d'; MUTED='#586d69'; LINE='#cbd5cc'; GREEN='#196d5e'; MINT='#a9ebcc'
COLORS=['#267765','#487da0','#ae7045','#7977a3','#558a56','#916783']
FONT='Microsoft YaHei UI'; MONO='Consolas'

def tm(samples):
    seconds=max(0,int(samples/RATE))
    return f'{seconds//60}:{seconds%60:02}'

def label(parent,text='',size=10,color=INK,bg=PAPER,bold=False,**kw):
    return tk.Label(parent,text=text,bg=bg,fg=color,font=(FONT,size,'bold' if bold else 'normal'),anchor='w',**kw)

def button(parent,text,command,bg=CARD,fg=INK):
    return tk.Button(parent,text=text,command=command,bg=bg,fg=fg,activebackground=MINT,activeforeground=INK,
                     relief='flat',bd=0,padx=11,pady=5,cursor='hand2',font=(FONT,10))

class Player:
    def __init__(self,root):
        self.root=root
        root.title('皇牌空战 8 · 动态音乐播放器')
        root.configure(bg=PAPER)
        width=min(1220,root.winfo_screenwidth()-40);height=min(830,root.winfo_screenheight()-85)
        root.geometry(f'{width}x{height}')
        root.minsize(min(width,840),min(height,590))
        self.data=json.loads((BASE/'library/score.json').read_text(encoding='utf8'))
        self.t=Transport(self.data);self.media=Media(BASE)
        self.lock=threading.RLock();self.events=queue.Queue();self.executor=ThreadPoolExecutor(1)
        self.output=Output(self.t,self.lock,lambda err:self.events.put(('error',err)))
        self.generation=0;self.ready=False;self.closed=False;self.peak_cache={};self.stamp=None
        self.phase_widgets=[];self.last_key=None;self.visible=[];self.songs=self.data['songs']
        self.query=tk.StringVar();self.filter=tk.StringVar(value='互动配乐')
        self.search_hint='搜索任务、曲目或编号'
        self.layout()
        self.refresh_library()
        self.choose(next((s for s in self.songs if s['event']=='AKE_Bgm_Ms29B_Play'),self.songs[0]))
        root.protocol('WM_DELETE_WINDOW',self.close)
        root.bind('<space>',self.space)
        root.after(40,self.tick)

    def layout(self):
        side=tk.Frame(self.root,bg=NAV,width=272)
        side.pack(side='left',fill='y');side.pack_propagate(False)
        branding=tk.Frame(side,bg=NAV);branding.pack(fill='x',padx=18,pady=(22,14))
        tk.Label(branding,text='ACE COMBAT 8',font=(MONO,19,'bold'),fg=MINT,bg=NAV).pack(anchor='w')
        label(branding,'音乐库',14,'#eef5ed',NAV,True).pack(anchor='w')
        self.search=tk.Entry(side,textvariable=self.query,bg=NAV2,fg='#f0f6ed',insertbackground=MINT,
                             relief='flat',font=(FONT,10),highlightthickness=1,highlightbackground='#395250',highlightcolor=MINT)
        self.search.pack(fill='x',ipady=6,padx=18)
        self.search.insert(0,self.search_hint);self.search.configure(fg='#adbfb8')
        self.search.bind('<FocusIn>',lambda e:self.search_placeholder(True))
        self.search.bind('<FocusOut>',lambda e:self.search_placeholder(False))
        self.query.trace_add('write',lambda *_:self.refresh_library())
        tabs=tk.Frame(side,bg=NAV);tabs.pack(fill='x',padx=18,pady=(12,8))
        self.tabs={}
        for k in ('互动配乐','单段配乐'):
            b=button(tabs,k,lambda k=k:self.set_filter(k),NAV2,'#dce5dd')
            b.pack(side='left',fill='x',expand=True,padx=(0,3));self.tabs[k]=b
        self.list=tk.Listbox(side,bg=NAV,fg='#dce5dd',selectbackground='#31564e',selectforeground='#c7f6d9',
                activestyle='none',exportselection=False,bd=0,highlightthickness=0,font=(FONT,11),selectborderwidth=3)
        self.list.pack(fill='both',expand=True,padx=12,pady=(4,0))
        list_scroll=ttk.Scrollbar(side,orient='horizontal',command=self.list.xview)
        self.list.configure(xscrollcommand=list_scroll.set)
        list_scroll.pack(fill='x',padx=12)
        self.list.bind('<<ListboxSelect>>',self.pick)
        self.count=label(side,'',9,'#9cb5aa',NAV);self.count.pack(fill='x',padx=18,pady=12)

        main=tk.Frame(self.root,bg=PAPER);main.pack(side='left',fill='both',expand=True,padx=25,pady=(16,14))
        head=tk.Frame(main,bg=PAPER);head.pack(fill='x')
        subtitle=label(head,'皇牌空战 8  /  动态配乐',10,MUTED);subtitle.pack(side='left')
        self.kind=label(head,'',9,GREEN);self.kind.pack(side='right')
        self.title=label(main,'',25,INK,bold=True,justify='left');self.title.pack(fill='x',pady=(3,0))
        self.detail=label(main,'',10,MUTED,justify='left');self.detail.pack(fill='x',pady=(0,12))
        main.bind('<Configure>',lambda e:[w.configure(wraplength=max(220,e.width-8)) for w in (self.title,self.detail)])
        self.sections_title=label(main,'段落',11,INK,bold=True);self.sections_title.pack(anchor='w')
        self.sections=tk.Frame(main,bg=PAPER);self.sections.pack(fill='x',pady=(5,10))
        self.phasebox=ttk.Combobox(self.sections,state='readonly',font=(FONT,10))
        self.phasebox.bind('<<ComboboxSelected>>',lambda e:self.request_section(self.song['order'][self.phasebox.current()]))
        controls=tk.Frame(main,bg=CARD,highlightthickness=1,highlightbackground=LINE)
        controls.pack(fill='x')
        self.sectionname=label(controls,'',11,GREEN,CARD,True);self.sectionname.pack(side='left',padx=(10,4),pady=8)
        self.repeat=tk.StringVar();self.duration=tk.StringVar()
        self.repbox=ttk.Combobox(controls,width=8,state='readonly',textvariable=self.repeat,
                    values=['游戏预设','1 遍','2 遍','4 遍','8 遍','持续循环'],font=(FONT,9))
        self.lenbox=ttk.Combobox(controls,width=8,state='readonly',textvariable=self.duration,font=(FONT,9))
        for title,box in [('循环次数',self.repbox),('每遍长度',self.lenbox)]:
            label(controls,title,9,MUTED,CARD).pack(side='left',padx=(12,4));box.pack(side='left')
            box.bind('<<ComboboxSelected>>',self.apply_controls)
        button(controls,'还原',self.reset,CARD,GREEN).pack(side='right')
        lanehead=tk.Frame(main,bg=PAPER);lanehead.pack(fill='x',pady=(12,5))
        self.lanetitle=label(lanehead,'分轨',11,INK,bold=True);self.lanetitle.pack(side='left')
        self.lanehint=label(lanehead,'',9,MUTED);self.lanehint.pack(side='right')
        self.chartframe=tk.Frame(main,bg=CARD,highlightthickness=1,highlightbackground=LINE)
        self.chartframe.pack(fill='both',expand=True)
        self.chart=tk.Canvas(self.chartframe,bg=CARD,bd=0,highlightthickness=0,width=400,height=220)
        self.scroll=ttk.Scrollbar(self.chartframe,orient='vertical',command=self.chart.yview)
        self.chart.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side='right',fill='y')
        self.chart.pack(fill='both',expand=True)
        self.chart.bind('<Configure>',lambda e:self.draw())
        self.chart.bind('<Button-1>',self.chart_click)
        self.chart.bind('<MouseWheel>',lambda e:self.chart.yview_scroll(-int(e.delta/120),'units'))
        self.note=label(main,'',9,MUTED);self.note.pack(fill='x',pady=(6,4))
        transport=tk.Frame(main,bg=NAV);transport.pack(fill='x')
        self.playbtn=button(transport,'▶  播放',self.play,MINT,INK);self.playbtn.pack(side='left',padx=(9,4),pady=9)
        button(transport,'下一段  ›',self.next,NAV2,'#e6eee4').pack(side='left',padx=3)
        self.time=label(transport,'0:00 / 0:00',11,'#e6eee4',NAV);self.time.pack(side='left',padx=10)
        self.vol=tk.Scale(transport,from_=0,to=100,orient='horizontal',showvalue=False,length=95,bd=0,
            highlightthickness=0,bg=NAV,troughcolor='#425753',activebackground=MINT,sliderrelief='flat',
            command=self.volume);self.vol.set(65);self.vol.pack(side='right',padx=(1,12))
        label(transport,'总音量',9,'#bbcfc3',NAV).pack(side='right')

    def search_placeholder(self,focus):
        if focus and self.query.get()==self.search_hint:
            self.query.set('');self.search.configure(fg='#f0f6ed')
        elif not focus and not self.query.get():
            self.query.set(self.search_hint);self.search.configure(fg='#adbfb8')

    def set_filter(self,k):
        self.filter.set(k);self.refresh_library()

    def refresh_library(self):
        if not hasattr(self,'list'):return
        q=self.query.get().strip().lower()
        if q==self.search_hint:q=''
        self.visible=[s for s in self.songs if s['bank']==self.filter.get() and
                      (not q or q in ' '.join([s['title'],s['event'],str(s['id']),s.get('context',''),
                          s.get('originalLabel',''),*s.get('relatedTitles',[])]).lower())]
        self.list.delete(0,'end')
        for s in self.visible:
            suffix=f"  +{len(s['relatedTitles'])}" if s.get('relatedTitles') else ''
            self.list.insert('end','  '+s['title']+suffix)
        for k,b in self.tabs.items():b.configure(bg=MINT if k==self.filter.get() else NAV2,fg=INK if k==self.filter.get() else '#dce5dd')
        self.count.configure(text=f'{len(self.visible)} 首{self.filter.get()}')
        self.mark_list()

    def mark_list(self):
        if getattr(self,'song',None) in self.visible:
            i=self.visible.index(self.song);self.list.selection_clear(0,'end');self.list.selection_set(i);self.list.see(i)

    def pick(self,e):
        selected=self.list.curselection()
        if selected:
            s=self.visible[selected[0]]
            if getattr(self,'song',None) is not s:self.choose(s)

    def choose(self,song):
        self.generation+=1;gen=self.generation
        self.ready=False;self.song=song;self.peak_cache.clear();self.mark_list()
        with self.lock:
            self.t.playing=False;self.t.audio={};self.t.select(song)
        self.output.flush.set()
        self.title.configure(text=song['title'])
        self.kind.configure(text=song['bank'])
        meta=[song.get('context',''),f"{len(song['order'])} 个段落",song['event']]
        if song.get('relatedTitles'):meta.insert(1,'同一编排：'+' / '.join(song['relatedTitles']))
        self.detail.configure(text='    ·    '.join(filter(None,meta)))
        self.playbtn.configure(state='disabled',text='载入中')
        self.make_phases();self.last_key=None;self.stamp=None
        self.paint_section()
        self.note.configure(text='正在读取本地分轨…')
        ids=self.t.assets(song)
        def load():
            try:
                ok=self.media.load(ids,lambda i,n:self.events.put(('progress',gen,i,n)),lambda:self.generation!=gen)
                if ok:self.events.put(('ready',gen))
            except Exception as exc:self.events.put(('failed',gen,str(exc)))
        self.executor.submit(load)

    def make_phases(self):
        for b in self.phase_widgets:b.destroy()
        self.phase_widgets=[];self.phasebox.pack_forget();order=self.song['order']
        def item(i,s):
            seg=self.t.segs[s];name=self.song['labels'][str(s)]
            return f'{i+1:02}   {name}   {tm(seg["exit"]-seg["entry"])}'
        if len(order)>8:
            self.phasebox.configure(values=[item(i,s) for i,s in enumerate(order)])
            self.phasebox.pack(fill='x')
        else:
            cols=min(len(order),4)
            for i in range(4):self.sections.columnconfigure(i,weight=(1 if i<cols else 0),uniform='phase')
            for i,s in enumerate(order):
                b=button(self.sections,item(i,s),lambda s=s:self.request_section(s))
                b.configure(padx=3,pady=4,font=(FONT,9))
                b.grid(row=i//cols,column=i%cols,padx=(0,4),pady=2,sticky='ew');self.phase_widgets.append(b)

    def request_section(self,sid):
        with self.lock:self.t.queue(sid)
        self.stamp=None;self.paint_section()
        self.status()

    def next(self):
        order=self.song['order']
        with self.lock:
            n=self.t.song['next'].get(str(self.t.key))
            if n is None:n=order[(order.index(self.t.key)+1)%len(order)] if self.t.key in order else order[0]
        self.request_section(n)

    def space(self,event):
        if event.widget not in (self.search,self.phasebox,self.repbox,self.lenbox):
            self.play();return 'break'

    def play(self):
        if not self.ready:return
        with self.lock:
            if self.t.finished:self.t.start(self.song['order'][0])
            self.t.playing=not self.t.playing
        self.status()

    def reset(self):
        with self.lock:
            sid=self.t.key
            self.t.settings.pop(sid,None)
            for l in self.t.segs[sid]['lanes']:self.t.set_lane(l,l['on'])
            self.t.replan()
        self.paint_section()

    def apply_controls(self,e):
        with self.lock:
            if self.t.active.get('bridge'):return
            rep={'游戏预设':None,'持续循环':0}.get(self.repeat.get())
            if self.repeat.get().endswith('遍'):rep=int(self.repeat.get().split()[0])
            length=None if self.duration.get()=='原长' else int(self.duration.get().split()[0])
            self.t.change(rep,length)
        self.draw();self.status()

    def volume(self,v):
        with self.lock:self.t.master=float(v)/100

    def paint_section(self):
        if not hasattr(self,'song'):return
        with self.lock:
            sid=self.t.key;s=self.t.segs[sid];q=self.t.queued
            rep,bars=self.t.settings.get(sid,(None,None))
            bridge=bool(self.t.active.get('bridge'))
        new=sid!=self.last_key
        self.last_key=sid;order=self.song['order']
        if len(order)>8 and sid in order:self.phasebox.current(order.index(sid))
        for i,b in enumerate(self.phase_widgets):
            active=order[i]==sid
            b.configure(bg=GREEN if active else '#dceadd' if order[i]==q else CARD,fg='white' if active else INK)
        self.sectionname.configure(text=self.song['labels'].get(str(sid),'过渡段'))
        self.repbox.configure(state='disabled' if bridge else 'readonly')
        self.lenbox.configure(state='readonly' if s['bar'] and not bridge else 'disabled')
        self.repeat.set('游戏预设' if rep is None else '持续循环' if rep==0 else f'{rep} 遍')
        mx=max(0,(s['exit']-s['entry']-1)//max(1,s['bar']))
        lengths=[1,2,4,8,16,32,64,128]
        self.lenbox.configure(values=['原长']+[f'{i} 小节' for i in lengths if i<=mx])
        self.duration.set('原长' if bars is None else f'{bars} 小节')
        phrase=self.song.get('sectionTitles',{}).get(str(sid))
        self.lanetitle.configure(text='分轨'+(' · '+phrase if phrase else ''))
        self.lanehint.configure(text=f"{len(s['lanes'])} 轨   ·   点击轨名开关，点击时间线定位" if not phrase else f"{len(s['lanes'])} 轨")
        if new:self.chart.yview_moveto(0)
        self.draw()

    def wave_peaks(self,s,lane):
        sig=(s['id'],lane['key'])
        if sig in self.peak_cache:return self.peak_cache[sig]
        bins=320;peaks=np.zeros(bins)
        if not self.ready:return peaks
        points=np.linspace(0,max(1,s['length']-1),bins*64,dtype=np.int64)
        vals=np.zeros(len(points))
        for c in lane['clips']:
            audio=self.t.audio.get(c['src'])
            if audio is None:continue
            mask=(points>=c['at']) & (points<c['end'])
            ix=(points[mask]-c['at']+c['offset'])%len(audio)
            vals[mask]=np.maximum(vals[mask],np.max(np.abs(audio[ix].astype(np.float32)),axis=1))
        peaks=np.sqrt(np.minimum(1,np.max(vals.reshape(bins,64),axis=1)))
        self.peak_cache[sig]=peaks
        return peaks

    def draw(self):
        if not getattr(self,'song',None):return
        c=self.chart;c.delete('all');w=c.winfo_width()
        if w<150:return
        with self.lock:
            sid=self.t.key;s=self.t.segs[sid];_,end=self.t.limits(sid)
            enabled=[self.t.enabled(l) for l in s['lanes']]
        self.left=106;self.right=w-14;span=max(1,self.right-self.left)
        x=lambda p:self.left+span*min(1,max(0,p/max(1,s['length'])))
        self.rowh=50;top=42;bottom=max(top+1,top+len(s['lanes'])*self.rowh)
        c.configure(scrollregion=(0,0,w,bottom+8),yscrollincrement=22)
        c.create_rectangle(0,0,w,top,fill='#eaf0e8',outline='')
        c.create_text(12,top/2,text='时间线',anchor='w',fill=MUTED,font=(FONT,9))
        for frac in (0,.25,.5,.75,1):
            xx=self.left+frac*span;anchor='w' if frac==0 else 'e' if frac==1 else 'center'
            c.create_text(xx,10,text=tm(frac*s['length']),anchor=anchor,fill=MUTED,font=(MONO,9))
        xe=x(s['entry']);xx=x(end)
        c.create_rectangle(xe,22,max(xe+1,xx),36,fill='#b9dcca',outline='')
        if xx-xe>60:c.create_text((xx+xe)/2,29,text='循环区间',fill='#245d50',font=(FONT,8))
        for _,p,_ in s['marks']:c.create_line(x(p),35,x(p),41,fill=GREEN)
        for i,l in enumerate(s['lanes']):
            y=top+i*self.rowh;mid=y+self.rowh/2;on=enabled[i];color=COLORS[i%len(COLORS)] if on else '#aeb9b2'
            c.create_line(0,y,w,y,fill=LINE)
            c.create_rectangle(self.left,y+3,self.right,y+self.rowh-3,fill='#f2f5ef',outline='')
            c.create_oval(10,mid-5,20,mid+5,outline=color,fill=color if on else CARD,width=2)
            name=f"版本 {l['variant']+1:02}" if l['variant'] is not None else f'分轨 {i+1:02}'
            c.create_text(29,mid-8,text=name,anchor='w',fill=INK if on else MUTED,font=(FONT,10,'bold'))
            c.create_text(29,mid+11,text=str(l['id']),anchor='w',fill=MUTED,font=(MONO,8))
            for clip in l['clips']:
                a=x(clip['at']);b=x(clip['end'])
                if b>a:c.create_rectangle(a,y+7,b,y+self.rowh-7,fill='#e1eae2' if on else '#e8ece7',outline='')
            peaks=self.wave_peaks(s,l)
            stripe=[]
            for j,amp in enumerate(peaks):
                xp=self.left+j*span/(len(peaks)-1);stripe.append((xp,mid-amp*18))
            if np.max(peaks)>0:
                coords=[v for pair in stripe+[(xp,2*mid-yp) for xp,yp in reversed(stripe)] for v in pair]
                c.create_polygon(*coords,fill=color,outline='')
            c.create_line(self.left,mid,self.right,mid,fill=color,width=1)
        if not s['lanes']:c.create_text(self.left,top+25,text='此段由游戏用作静音衔接',anchor='w',fill=MUTED,font=(FONT,10))
        c.create_line(xe,36,xe,bottom,fill='#65a694',dash=(3,4))
        c.create_line(xx,36,xx,bottom,fill='#65a694',dash=(3,4))
        self.cursor=c.create_line(self.left,18,self.left,bottom,fill='#d56b3b',width=2)
        self.cursor_bottom=bottom
        self.cursor_pos()

    def cursor_pos(self):
        if not hasattr(self,'cursor'):return
        with self.lock:pos=self.t.position();s=self.t.segs[self.t.key]
        xp=self.left+(self.right-self.left)*min(1,pos/max(1,s['length']))
        self.chart.coords(self.cursor,xp,18,xp,self.cursor_bottom)
        self.time.configure(text=tm(pos)+' / '+tm(s['length']))

    def chart_click(self,e):
        if not self.ready or not hasattr(self,'left'):return
        with self.lock:
            s=self.t.segs[self.t.key];y=self.chart.canvasy(e.y)
            if e.x<self.left:
                i=int((y-42)//self.rowh)
                if 0<=i<len(s['lanes']):self.t.toggle(s['lanes'][i])
            elif self.t.key in self.song['order']:
                pos=round(s['length']*max(0,min(1,(e.x-self.left)/max(1,self.right-self.left))))
                self.output.flush.set();self.t.start(self.t.key,min(pos,s['length']-1))
        self.draw();self.status()

    def status(self):
        if not self.ready:return
        with self.lock:
            t=self.t;s=t.segs[t.key];rep,end=t.limits(t.key)
            text='已播完' if t.finished else '播放中' if t.playing else '已暂停'
            if t.queued:
                dest=self.song['labels'].get(str(t.queued),'下一段')
                name={0:'即时',1:'网格点',2:'小节',3:'节拍',5:'提示点',7:'出口'}.get(t.rule(t.key,t.queued)['sync'],'出口')
                text+=f'   ·   将在{name}切换至 {dest}'
            elif t.active.get('bridge'):text+='   ·   正在播放游戏过渡段'
            else:text+=f'   ·   第 {t.run} 遍 / '+('∞' if rep==0 else str(rep))
            text+=f"   ·   区间 {tm(s['entry'])} – {tm(end)}"
            if s['tempo']:text+=f"   ·   {s['tempo']:g} BPM"
            self.note.configure(text=text)
            self.playbtn.configure(text='Ⅱ  暂停' if t.playing else '▶  播放',state='normal')

    def tick(self):
        if self.closed:return
        while True:
            try:ev=self.events.get_nowait()
            except queue.Empty:break
            if ev[0]=='error':
                messagebox.showerror('音频输出',ev[1],parent=self.root)
            elif ev[1]==self.generation:
                if ev[0]=='ready':
                    with self.lock:self.t.audio=dict(self.media.files)
                    self.ready=True;self.paint_section();self.status()
                elif ev[0]=='progress':
                    self.note.configure(text=f'正在读取分轨素材   {ev[2]+1} / {ev[3]}')
                elif ev[0]=='failed':
                    self.note.configure(text=ev[2]);self.playbtn.configure(text='载入失败')
        if self.ready:
            with self.lock:sid=self.t.key
            if sid!=self.last_key:self.paint_section()
            self.cursor_pos();self.status()
        self.root.after(65,self.tick)

    def close(self):
        self.closed=True;self.generation+=1
        self.output.close()
        self.executor.shutdown(wait=True,cancel_futures=True)
        self.media.close();self.root.destroy()

if __name__=='__main__':
    try:ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:pass
    root=tk.Tk()
    try:
        Player(root);root.mainloop()
    except Exception as exc:
        messagebox.showerror('播放器启动失败',str(exc),parent=root)

