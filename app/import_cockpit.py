"""Build the player's 48 kHz cockpit cues from locally imported Wwise media."""
import json
import math
import subprocess
import wave

import numpy as np

RATE = 48000
# Each layer is (media ID, gain, delay in seconds, optional loop duration).
MIXES = {
    'missile_voice': [(927595354,1,0)], 'caution': [(547171836,1,0)],
    'rail_voice': [(950994608,1,0)], 'pull_voice': [(581569380,1,0)], 'stall_voice': [(522489780,1,0)],
    'danger': [(130377743,1,0,4)], 'pull_tone': [(189647210,1,0,12)],
    'rail_tone': [(395316048,.77,0),(796369815,.43,0)],
    'launch1': [(337949418,.9,0),(771165810,.53,.13),(128208147,.65,0)],
    'launch2': [(250623323,.9,0),(873934315,.53,.13),(92090115,.65,0)],
    'boom1': [(449364372,.92,0),(1063408987,.73,0),(332236888,.38,.03)],
    'boom2': [(296837470,.92,0),(979112304,.73,0),(123309554,.38,.03)],
    'gun_start': [(382026268,.85,0),(46882302,.55,.10)],
    'gun_body': [(104111638,.87,0,3.7),(428971644,.57,0,3.7),(367125748,.40,0,3.7)],
    'gun_tail': [(573215623,1,0),(656023017,.76,0)],
    'engine_surge': [(452627393,.60,0),(1048405253,.78,.08)],
    'engine_recovery': [(728417227,.65,0),(478832551,.53,.10)],
    'missile_pass1': [(735107832,1,0)], 'missile_pass2': [(512147323,1,0)],
    'missile_hit': [(569074443,.85,0),(756399073,.64,0),(176293869,.30,0)],
    # Two stereo variations in ThunderCloud_Inside_Ambience's quieter layer.
    'cloud_inside1': [(706171595,1,0)], 'cloud_inside2': [(219973025,1,0)],
}
LIMITS = dict(engine_surge=2.25,engine_recovery=2.9,missile_pass1=4.85,missile_pass2=6.38,missile_hit=6.15)
ENGINES = dict(engine_low=844099875,engine_cruise=542918694,engine_high=522259649)
SOURCE_IDS = set(ENGINES.values()) | {layer[0] for layers in MIXES.values() for layer in layers}


def decode(decoder, source, scratch):
    flags = getattr(subprocess,'CREATE_NO_WINDOW',0)
    meta=json.loads(subprocess.check_output([decoder,'-m','-I',source],creationflags=flags))
    wav=scratch/'decoded.wav'
    subprocess.run([decoder,'-i','-o',wav,source],capture_output=True,check=True,creationflags=flags)
    with wave.open(str(wav),'rb') as w:
        if w.getsampwidth()!=2:raise ValueError('座舱素材的采样格式不兼容')
        audio=np.frombuffer(w.readframes(w.getnframes()),'<i2').reshape(-1,w.getnchannels())/32768.
        return audio,w.getframerate(),meta.get('loopingInfo')


def periodic_resample(audio, rate):
    if rate==RATE:return audio
    divisor=math.gcd(rate,RATE);up=RATE//divisor;down=rate//divisor
    half=10*max(up,down)
    taps=np.sinc(np.arange(-half,half+1)/max(up,down))*np.kaiser(2*half+1,5.)
    taps*=up/taps.sum()
    positions=np.arange((len(audio)*up+down-1)//down)*down
    centers,phases=np.divmod(positions,up)
    out=np.zeros((len(centers),audio.shape[1]))
    for offset in range(-half//up,half//up+2):
        ix=half+phases-offset*up
        valid=(ix>=0)&(ix<len(taps))
        out[valid]+=audio[(centers[valid]+offset)%len(audio)]*taps[ix[valid],None]
    return out


def engine_loop(name,audio,rate,points):
    loop=periodic_resample(audio[points['start']:points['end']],rate)
    n=round(.04*RATE)
    t=np.linspace(0,1,n)[:,None];t=t*t*(3-2*t)
    loop=np.concatenate([loop[n:-n],loop[-n:]*(1-t)+loop[:n]*t])
    if name=='engine_low':
        old=len(loop);length=round(old*2**(150/1200))
        spectrum=np.fft.rfft(loop,axis=0)
        if old%2==0:spectrum[-1]*=.5
        loop=np.fft.irfft(spectrum,n=length,axis=0)*(length/old)*10**(-7.32/20)
    centers={'engine_low':(2510,3980),'engine_cruise':(1875,5270),'engine_high':(2415,6810)}
    hz=np.fft.rfftfreq(len(loop),1/RATE);slope=(hz/1750)**3
    db=-(3.0 if name=='engine_low' else 5.5)*slope/(1+slope)
    for center in centers[name]:db-=3.5*np.exp(-.5*((hz-center)/(.12*center))**2)
    out=np.fft.irfft(np.fft.rfft(loop,axis=0)*(10**(db/20))[:,None],n=len(loop),axis=0)
    return np.rint(np.clip(out,-1,32767/32768)*32768).astype('<i2')


def render_cue(name,decoder,scratch,destination):
    def load(key):return decode(decoder,scratch/(str(key)+'.wem'),scratch)
    if name in ENGINES:pcm=engine_loop(name,*load(ENGINES[name]))
    else:
        layers=[]
        for key,gain,delay,*duration in MIXES[name]:
            audio,rate,points=load(key)
            if rate!=RATE:
                at=np.arange(round(len(audio)*RATE/rate))*rate/RATE
                audio=np.column_stack([np.interp(at,np.arange(len(audio)),ch) for ch in audio.T])
            if duration:
                start,end=(round(points[k]*RATE/rate) for k in ('start','end'))
                ix=np.arange(round(duration[0]*RATE))
                ix[ix>=end]=start+(ix[ix>=end]-start)%(end-start)
                audio=audio[ix]
            layers.append((audio,gain,round(delay*RATE)))
        length=max(len(a)+pos for a,_,pos in layers)
        limited=name in LIMITS
        if limited:length=min(length,round(LIMITS[name]*RATE))
        out=np.zeros((length,2 if limited else max(a.shape[1] for a,_,_ in layers)))
        for a,gain,pos in layers:
            take=min(len(a),length-pos)
            if take>0:out[pos:pos+take]+=gain*a[:take]
        if limited:
            fade=min(len(out),round(.12*RATE))
            out[-fade:]*=np.linspace(1,0,fade)[:,None]
            out[:48]*=np.linspace(0,1,48)[:,None]
        out*=min(1.,(.86 if limited else .82)/max(.0001,np.abs(out).max()))
        pcm=np.rint(out*32767).astype('<i2')
    with wave.open(str(destination),'wb') as w:
        w.setparams((pcm.shape[1],2,RATE,0,'NONE','not compressed'))
        w.writeframes(pcm.tobytes())
