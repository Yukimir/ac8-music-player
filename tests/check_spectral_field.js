const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../desktop/ui/spectral-field.js'), 'utf8');
const Field = vm.runInNewContext(source + '\nSpectralField');
const field = new Field();
const state = {playing:true, ready:true, song:1, spectrumEpoch:1, spectrumHopMs:20};
function feed(sequence, bytes) {
  field.ingest({...state, spectralFrames:[[sequence, bytes]]}, sequence*20);
  field.advance(sequence*20, 20);
}
function sample(age, band) {
  const result = {};
  field.sample(age, band, result);
  return result;
}
const levels = [0, 16, 64, 128, 192, 254, 255];
for (let seq=0; seq<100; seq++) feed(seq, Array(56).fill(0));
for (let i=0; i<levels.length; i++) {
  const level = levels[i], row = Array(56).fill(0);
  row[21] = level;
  feed(100+i, row);
  assert(Math.abs(sample(0,21).energy - level/255) < 1e-6);
  if (i) assert(Math.abs(sample(1,21).energy - levels[i-1]/255) < 1e-6);
}
// Simultaneous bass and treble keep separate hues and the gap stays dark.
const chord = Array(56).fill(0);
chord[3]=210; chord[48]=200;
feed(107,chord);
assert(sample(0,3).tone < .10 && sample(0,48).tone > .82);
assert(sample(0,3).heat > .8 && sample(0,48).heat > .8);
assert.equal(sample(0,24).heat,0);
feed(108,Array(56).fill(0));
assert.equal(sample(0,48).energy,0);
assert(Math.abs(sample(.5,48).energy - 100/255) < 1e-6);
field.ingest({...state, playing:false, spectrumEpoch:2},2200);
assert.equal(sample(0,3).energy,0);
console.log('Passed full-scale linear levels, 20 ms transients, separate hotspots and reset.');
