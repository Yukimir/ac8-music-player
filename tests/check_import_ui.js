const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../desktop/ui/library-import.js'),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));

async function run() {
  const nodes=new Map();
  const get=id=>{
    if (!nodes.has(id)) nodes.set(id,{value:'',open:false,focus(){},showModal(){this.open=true;},close(){this.open=false;}});
    return nodes.get(id);
  };
  const window={}, calls=[];
  vm.runInNewContext(source,{window,document:{getElementById:get},setTimeout:fn=>setImmediate(fn)});
  let state={phase:'missing',configured:false,game:'D:/AC8',bytes:3e9,nativeBytes:4e9,extract:false,native:false};
  const api=async(op,args)=>{
    calls.push(op);
    if (op==='importBrowse') return 'E:/AC8';
    if (op==='importStart') {
      if (args.game==='bad') return {...state,phase:'error',message:'无效目录'};
      state={...state,...args,configured:true,phase:args.extract?'running':'ready'};
    } else if (op==='importStatus' && state.phase==='running') state.phase='ready';
    return {...state};
  };
  const submit=()=>get('importForm').onsubmit({preventDefault(){}});
  const setup=window.prepareAudioLibrary(api);
  await tick();
  assert(get('libraryImport').open && get('readDirect').checked && get('extractNative').disabled);
  assert.deepEqual(calls,['importStatus']);
  get('importPath').value='bad';await submit();
  assert.equal(get('importMessage').textContent,'无效目录');
  await get('importBrowse').onclick();await submit();await setup;
  assert.equal(state.game,'E:/AC8');assert(!state.extract && !state.native);
  await window.prepareAudioLibrary(api);assert(!get('libraryImport').open);
  const change=window.prepareAudioLibrary(api,true);await tick();
  get('readExtract').checked=true;get('readDirect').checked=false;get('readExtract').onchange();
  assert(!get('extractNative').disabled);get('extractNative').checked=true;
  await submit();await change;
  assert(state.extract && state.native && !get('libraryImport').open);
  const cancel=window.prepareAudioLibrary(api,true);await tick();
  assert(get('readExtract').checked && get('extractNative').checked);
  get('importPath').value='unsaved';get('importCancel').onclick();await cancel;
  assert.equal(state.game,'E:/AC8');
  console.log('Passed direct first run, retry, cached startup, optional extraction and settings cancellation.');
}
run().catch(e=>{console.error(e);process.exitCode=1;});
