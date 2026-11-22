const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');

const source=fs.readFileSync(path.join(__dirname,'../desktop/ui/terrain-camera.js'),'utf8');
const Camera=vm.runInNewContext(source+'\nTerrainCamera');
const camera=new Camera();
const queue=[], moves=Camera.shots.map(()=>new Set());
assert.equal(camera.advance(100),Camera.standard);
camera.setAuto(true);
for (let i=0;i<Camera.shots.length*Camera.movements.length;i++) {
  const angle=camera.shot, move=camera.movement, first=camera.pose;
  queue.push(angle);
  moves[angle].add(move);
  camera.advance(4500);
  assert.notDeepEqual(first,camera.pose);
  if (move==='cruise') {
    assert(camera.pose.forward>first.forward);
    assert.equal(camera.pose.vx,first.vx);
  }
  assert(Math.min(camera.pose.left,camera.pose.right)>camera.pose.vy);
  camera.advance(camera.duration-camera.elapsed);
  assert.notEqual(camera.shot,angle);
  assert.equal(camera.elapsed,0);
  assert.deepEqual(camera.pose,camera.movingPose());
  assert.equal(Boolean(camera.pose.side),Boolean(Camera.shots[camera.shot].side));
}
for (let i=0;i<queue.length;i+=5) assert.equal(new Set(queue.slice(i,i+5)).size,5);
assert(queue.every((angle,i)=>!i || angle!==queue[i-1]));
assert(moves.every(set=>set.size===3));
assert(Camera.shots.some(pose=>pose.side));
camera.setAuto(false);
assert.equal(camera.advance(90000),Camera.standard);

// Poll across a pickup, a loop origin reset each bar and a tempo change. All
// observed cuts must belong to the active segment's sample-clock grid.
function verifyGrid(barAt,originAt) {
  const cam=new Camera();
  cam.setAuto(true);
  const cuts=[];
  for (let ms=0;ms<=62000;ms+=40) {
    const clock=48*ms, bar=barAt(clock), gridOrigin=originAt(clock,bar);
    const state={song:1,spectrumEpoch:1,ready:true,playing:true,clock,gridOrigin,bar,sampleRate:48000};
    const before=cam.sequence;
    cam.sync(state);
    cam.sync(state);
    cam.advance(40);
    if (before===cam.sequence) continue;
    assert.equal(cam.sequence,before+1);
    assert(clock>=gridOrigin);
    assert((clock-gridOrigin)%bar<1920);
    cuts.push(ms);
  }
  assert(cuts.length>=3);
  assert(cuts.every((time,i)=>time-(cuts[i-1] || 0)>=9400));
  const before=cam.sequence;
  cam.sync({song:1,spectrumEpoch:2,ready:true,playing:true,clock:0,gridOrigin:-222000,bar:96000,sampleRate:48000});
  assert.equal(cam.sequence,before);
}
verifyGrid(()=>96000,()=>14400);
verifyGrid(clock=>clock<1440000?128000:72000,(clock,bar)=>{
  const first=clock<1440000?0:1440000;
  return first+Math.floor((clock-first)/bar)*bar;
});

// Feed duplicated 20 ms FFT packets. Steady half-second attacks must trigger
// on an attack. A track made entirely of silence must keep the timed fallback.
for (const rhythmic of [true,false]) {
  const cam=new Camera();
  cam.setAuto(true);
  cam.duration=10250;
  const before=cam.sequence;
  let cutTime;
  for (let time=0;time<=12000;time+=20) {
    const beat=time%500===0;
    const bands=Array(56).fill(rhythmic && beat?190:40);
    const state={playing:true,ready:true,song:2,clock:time*48,spectrumEpoch:1,
      bar:0,spectrumHopMs:20,spectralFrames:[[time/20,bands]]};
    cam.sync(state);cam.sync(state);cam.advance(20);
    if (cam.sequence!==before) {cutTime=time;break;}
  }
  assert.equal(cutTime,rhythmic?10500:10240);
}
console.log('Passed 15 camera combinations, hard cuts, pickup/loop/tempo synchronization, pulse detection and silent fallback.');
