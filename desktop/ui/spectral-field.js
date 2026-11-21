'use strict';

// A horizontal slice is frequency. Successive slices form the depth of the map.
// The audio engine produces one slice per 40 ms of output, including silent ones.
class SpectralField {
  constructor() {
    this.bands = 56;
    this.capacity = 56;
    this.rows = Array.from({length:this.capacity}, () => ({
      number:-1, energy:new Float32Array(this.bands), tone:new Float32Array(this.bands)
    }));
    this.kernel = Array.from({length:19}, (_,i) => Math.exp(-.5*((i-9)/3.3)**2));
    this.newest = -1;
    this.cursor = -1;
    this.gain = 0;
    this.playing = false;
    this.lastPacket = 0;
  }

  ingest(state, now) {
    this.playing = state.playing && state.ready && !state.error;
    if (!this.playing) return;
    if (state.spectrumEpoch !== this.epoch || state.song !== this.song) {
      this.epoch = state.spectrumEpoch;
      this.song = state.song;
      this.clear();
    }
    const frames = state.spectralFrames;
    if (!frames || !frames.length) return;
    if (this.newest >= 0 && frames[0][0] > this.newest+1) this.clear();
    for (const [number, bytes] of frames) {
      if (number<=this.newest) continue;
      this.store(number,bytes);
      if (this.cursor<0) this.cursor=number;
      this.newest=number;
      this.lastPacket=now;
    }
  }

  clear() {
    for (const row of this.rows) row.number=-1;
    this.newest=this.cursor=-1;
    this.gain=0;
  }

  store(number, bytes) {
    const row=this.rows[number%this.capacity], size=this.bands;
    const previous=this.rows[(number-1+this.capacity)%this.capacity];
    const contiguous=previous.number===number-1;
    row.number=number;
    let sum=0, moment=0;
    const power=Float32Array.from(bytes,value=>(value/255)**3);
    for (let i=0;i<size;i++) { sum+=power[i]; moment+=power[i]*i; }
    const center=sum ? moment/sum : (size-1)/2;
    for (let i=0;i<size;i++) {
      let local=0, localMoment=0, weight=0;
      const lo=Math.max(0,i-9), hi=Math.min(size-1,i+9);
      for (let j=lo;j<=hi;j++) {
        const k=this.kernel[j-i+9], p=power[j]*k;
        local+=p; localMoment+=p*j; weight+=k;
      }
      // Overlapping bands let a changing instrument shift the hue of the same vertex.
      const smooth=Math.cbrt(local/weight);
      const energy=.76*smooth+.24*bytes[i]/255;
      const tone=(.94*(local ? localMoment/local : i)+.06*center)/(size-1);
      const before=contiguous?previous.energy[i]:0;
      const attack=energy>before?.72:.36;
      const held=before*(1-attack), fresh=energy*attack;
      row.energy[i]=held+fresh;
      row.tone[i]=(held+fresh)>0 ? (held*previous.tone[i]+fresh*tone)/(held+fresh) : tone;
    }
  }

  advance(now, elapsed) {
    const live=this.playing && this.newest>=0 && now-this.lastPacket<420;
    if (live) {
      // Draw at audio time between IPC packets. Keep at most two slices in reserve.
      const distance=this.newest-this.cursor;
      this.cursor=Math.min(this.newest,this.cursor + elapsed/40 + Math.max(0,distance-2)*.22);
    }
    const target=live?1:0, tau=live?100:205;
    this.gain+=(target-this.gain)*(-Math.expm1(-elapsed/tau));
    if (!live && this.gain<.003) this.gain=0;
    return live || this.gain>0;
  }

  sample(age, band, out) {
    if (!this.gain) {out.energy=0;out.tone=0;return;}
    const pos=this.cursor-age, n=Math.floor(pos), phase=pos-n;
    const i=Math.floor(band), j=Math.min(i+1,this.bands-1), u=band-i;
    let energy=0, moment=0;
    for (let k=0;k<2;k++) {
      const seq=n+k;
      if (seq<0) continue;
      const row=this.rows[seq%this.capacity];
      if (row.number!==seq) continue;
      const w=k?phase:1-phase;
      const left=(1-u)*row.energy[i]*w, right=u*row.energy[j]*w;
      energy+=left+right;
      moment+=left*row.tone[i]+right*row.tone[j];
    }
    out.energy=energy*this.gain;
    out.tone=energy>0 ? moment/energy : 0;
  }
}
