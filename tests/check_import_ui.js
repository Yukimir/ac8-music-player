const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../desktop/ui/library-import.js'),'utf8');
async function run(mode) {
  const ui=new Map();
  const get=id=>{
    if (!ui.has(id)) ui.set(id,{value:'',open:false,focus(){},showModal(){this.open=true;},close(){this.open=false;}});
    return ui.get(id);
  };
  const window={};
  vm.runInNewContext(source,{window,document:{getElementById:get},setTimeout:fn=>setImmediate(fn)});
  const found=mode==='automatic'?'D:/Steam/steamapps/common/ACE COMBAT 8':'';
  let phase=mode==='cached'?'ready':'missing',calls=[];
  const api=async(op,arg)=>{
    calls.push(op);
    if (op==='importBrowse') return 'E:/Games/ACE COMBAT 8';
    if (op==='importStart') phase=arg.game==='bad'?'error':'running';
    else if (op==='importStatus' && phase==='running') phase='ready';
    return {phase,game:found,done:0,total:283,bytes:3e9,message:phase==='error'?'游戏文件不匹配':''};
  };
  const done=window.prepareAudioLibrary(api);
  await new Promise(resolve=>setImmediate(resolve));
  if (mode==='manual') {
    assert(get('libraryImport').open);get('importPath').value='bad';
    get('importForm').onsubmit({preventDefault(){}});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(get('importMessage').textContent,'游戏文件不匹配');
    await get('importBrowse').onclick();assert.equal(get('importPath').value,'E:/Games/ACE COMBAT 8');
    get('importForm').onsubmit({preventDefault(){}});
  }
  await done;
  assert(!get('libraryImport').open);
  assert.equal(calls.filter(x=>x==='importStart').length,{manual:2,automatic:1,cached:0}[mode]);
}
(async()=>{for(const mode of ['manual','automatic','cached']) await run(mode);console.log('Passed folder selection, retry, automatic Steam import and cached startup');})().catch(e=>{console.error(e);process.exitCode=1;});
