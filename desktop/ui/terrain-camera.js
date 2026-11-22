'use strict';

// Tracks with no authored tempo can still have a repeating percussive pulse.
// Accept a transient only after several recent attacks agree on its spacing.
class TerrainPulse {
  constructor() { this.reset(); }

  reset() {
    this.number=-1;
    this.previous=null;
    this.mean=0;
    this.attacks=[];
    this.lastPulse=-Infinity;
  }

  ingest(frames,hop) {
    let pulse=false;
    for (const [number,bands] of frames) {
      if (number<=this.number) continue;
      if (number!==this.number+1) this.reset();
      const time=number*hop;
      let flux=0;
      if (this.previous) {
        for (let i=0;i<bands.length;i++) flux+=Math.max(0,bands[i]-this.previous[i]);
        flux/=bands.length;
      }
      this.previous=bands;
      this.number=number;
      const threshold=Math.max(2.8,2.3*this.mean);
      this.mean+=(flux-this.mean)*(1-Math.exp(-hop/340));
      const last=this.attacks.at(-1);
      if (flux<threshold || time-last<235) continue;
      this.attacks.push(time);
      if (this.attacks.length>7) this.attacks.shift();
      const period=time-last;
      if (period<300 || period>900) continue;
      let matches=0;
      for (const attack of this.attacks.slice(0,-1)) {
        const beats=Math.round((time-attack)/period);
        if (beats>=1 && beats<=8 && Math.abs(time-attack-beats*period)<=Math.min(65,.12*period)) matches++;
      }
      if (matches>=3) {this.lastPulse=time;pulse=true;}
    }
    return pulse;
  }
}

class TerrainCamera {
  static standard={vx:.497,vy:.34,left:.495,right:.491,cell:.0293,aimX:.497};
  static shots=[
    {vx:.16,vy:.24,left:.515,right:.418,cell:.032,aimX:.51},
    {vx:.81,vy:.25,left:.423,right:.528,cell:.032,aimX:.51},
    {vx:.48,vy:.075,left:.36,right:.355,cell:.0255,aimX:.49},
    {vx:.44,vy:.42,left:.57,right:.563,cell:.033,aimX:.50},
    {vx:.50,vy:.21,left:.45,right:.45,cell:.024,aimX:.50,side:true}
  ];
  static movements=['cruise','truck','pan'];

  constructor() {
    this.auto=false;
    this.pose=TerrainCamera.standard;
    this.shot=-1;
    this.sequence=0;
    this.angles=[];
    this.moves=TerrainCamera.shots.map(()=>[]);
    this.pulse=new TerrainPulse();
  }

  setAuto(enabled) {
    if (enabled===this.auto) return;
    this.auto=enabled;
    this.lastClock=null;
    this.grid=false;
    this.listening=false;
    this.pulse.reset();
    if (enabled) this.nextShot();
    else this.pose=TerrainCamera.standard;
  }

  // Draw without replacement. A new angle cycle cannot open on the last angle.
  drawFrom(bag,options,previous) {
    if (!bag.length) {
      bag.push(...options);
      for (let i=bag.length-1;i>0;i--) {
        const j=Math.floor(Math.random()*(i+1));
        [bag[i],bag[j]]=[bag[j],bag[i]];
      }
      const last=bag.length-1;
      if (bag[last]===previous) [bag[0],bag[last]]=[bag[last],bag[0]];
    }
    return bag.pop();
  }

  nextShot() {
    this.shot=this.drawFrom(this.angles,TerrainCamera.shots.keys(),this.shot);
    this.movement=this.drawFrom(this.moves[this.shot],TerrainCamera.movements);
    this.direction=Math.random()<.5?-1:1;
    this.duration=9500+Math.random()*4500;
    this.elapsed=0;
    this.dueClock=null;
    this.sequence++;
    this.pose=this.movingPose();
  }

  movingPose() {
    const pose={...TerrainCamera.shots[this.shot]};
    const progress=this.elapsed/this.duration;
    // Leave room to keep moving while waiting for the next musical boundary.
    const travel=progress<=1 ? progress-.5 : .5+.4*(1-Math.exp(-(progress-1)/.4));
    const offset=travel*this.direction;
    if (this.movement==='cruise') {
      // Translate the eye toward the far rim in ground coordinates. Nearby
      // rows pass the viewer faster while the horizon and heading hold steady.
      pose.forward=.16*travel;
    } else if (this.movement==='truck') {
      pose.aimX+=.16*offset;
    } else {
      pose.vx+=.16*offset;
      pose.aimX+=.10*offset;
      pose.left+=.022*offset;
      pose.right-=.022*offset;
    }
    return pose;
  }

  sync(state) {
    if (!this.auto) return;
    const playing=state.playing && state.ready && !state.error;
    const grid=Boolean(playing && state.bar>0 && state.sampleRate>0 && Number.isFinite(state.gridOrigin));
    const discontinuity=state.song!==this.song || state.spectrumEpoch!==this.epoch ||
      state.clock<this.lastClock || this.grid!==grid || !playing;
    if (discontinuity) {
      this.dueClock=null;
      this.lastClock=null;
      this.pulse.reset();
      this.listening=false;
    }
    this.song=state.song;
    this.epoch=state.spectrumEpoch;
    this.grid=grid;
    if (grid) {
      const {clock,gridOrigin,bar,sampleRate}=state;
      const downbeat=gridOrigin+Math.floor((clock-gridOrigin)/bar)*bar;
      if (this.dueClock!==null && downbeat>=gridOrigin && downbeat>this.lastClock &&
          downbeat>=this.dueClock && clock-downbeat<sampleRate*.20) this.nextShot();
      if (this.dueClock===null) {
        this.dueClock=clock+Math.max(0,this.duration-this.elapsed)*sampleRate/1000;
      }
      this.lastClock=clock;
    } else if (playing) {
      const hop=state.spectrumHopMs || 20;
      const hit=this.pulse.ingest(state.spectralFrames || [],hop);
      this.listening=this.pulse.number*hop-this.pulse.lastPulse<1800;
      if (hit && this.elapsed>=this.duration) this.nextShot();
    }
  }

  advance(ms) {
    if (!this.auto) return this.pose;
    this.elapsed+=ms;
    if (!this.grid && this.elapsed>=this.duration+(this.listening?900:0)) this.nextShot();
    return this.pose=this.movingPose();
  }
}
