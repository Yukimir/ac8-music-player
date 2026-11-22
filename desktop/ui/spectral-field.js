'use strict';

// A horizontal slice is frequency. Successive slices form the depth of the map.
// Each numbered slice is one audio hop, including silence.
class SpectralField {
  constructor() {
    this.bands = 56;
    this.capacity = 56;
    this.rows = Array.from({length:this.capacity}, () => ({
      number:-1, energy:new Float32Array(this.bands), tone:new Float32Array(this.bands),
      heat:new Float32Array(this.bands)
    }));
    this.hop = 20;
    this.kernel = Array.from({length:11}, (_,i) => Math.exp(-.5*((i-5)/2.2)**2));
    // The analyzer encodes -70..0 dBFS. Use actual spectral power when
    // locating the dominant frequency, while retaining its level for height.
    this.power = Array.from({length:256}, (_,i) => 10**((i/255-1)*7));
    this.newest = -1;
    this.cursor = -1;
    this.gain = 0;
    this.playing = false;
    this.lastPacket = 0;
  }

  ingest(state, now) {
    this.playing = state.playing && state.ready && !state.error;
    this.hop = state.spectrumHopMs || 20;
    if (state.spectrumEpoch !== this.epoch || state.song !== this.song) {
      this.epoch = state.spectrumEpoch;
      this.song = state.song;
      this.clear();
    }
    if (!this.playing) return;
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
    row.number=number;
    const loudest=Math.max(...bytes);
    for (let i=0;i<size;i++) {
      let local=0, moment=0, crest=bytes[i];
      const lo=Math.max(0,i-5), hi=Math.min(size-1,i+5);
      for (let j=lo;j<=hi;j++) {
        const p=this.power[bytes[j]]*this.kernel[j-i+5];
        local+=p; moment+=p*j;
        crest=Math.max(crest,bytes[j]-9*Math.abs(j-i));
      }
      row.energy[i]=bytes[i]/255;
      row.tone[i]=moment/local/(size-1);
      // Color the peaks in this slice. Keep weaker bands visible in the
      // cool floor light and avoid amplifying noise in very quiet passages.
      const strength=.45*bytes[i]+.55*crest;
      const proximity=Math.max(0,1-(loudest-strength)/100);
      row.heat[i]=Math.min(1,strength/100)*proximity;
    }
  }

  advance(now, elapsed) {
    const live=this.playing && this.newest>=0 && now-this.lastPacket<420;
    if (live) {
      // Interpolate at the audio clock between packets; catch up after UI stalls.
      const distance=this.newest-this.cursor;
      this.cursor=Math.min(this.newest,this.cursor + elapsed/this.hop + Math.max(0,distance-3)*.35);
    }
    const target=live?1:0, tau=live?40:125;
    this.gain+=(target-this.gain)*(-Math.expm1(-elapsed/tau));
    if (!live && this.gain<.003) this.gain=0;
    return live || this.gain>0;
  }

  sample(age, band, out) {
    if (!this.gain) {out.energy=0;out.tone=0;out.heat=0;return;}
    const pos=this.cursor-age, n=Math.floor(pos), phase=pos-n;
    const i=Math.floor(band), j=Math.min(i+1,this.bands-1), u=band-i;
    let energy=0, moment=0, heat=0;
    for (let k=0;k<2;k++) {
      const seq=n+k;
      if (seq<0) continue;
      const row=this.rows[seq%this.capacity];
      if (row.number!==seq) continue;
      const w=k?phase:1-phase;
      const left=(1-u)*row.energy[i]*w, right=u*row.energy[j]*w;
      energy+=left+right;
      moment+=left*row.tone[i]+right*row.tone[j];
      heat+=w*((1-u)*row.heat[i]+u*row.heat[j]);
    }
    out.energy=energy*this.gain;
    out.heat=heat*this.gain;
    out.tone=energy>0 ? moment/energy : 0;
  }
}
