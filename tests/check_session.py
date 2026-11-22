"""Cold-start the Windows audio worker and Web UI against the same WebView storage.

Requires Playwright, Edge, a portable release ZIP and a game installation.
"""
import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
import tempfile
from zipfile import ZipFile

from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--archive',required=True,type=Path)
parser.add_argument('--game',required=True,type=Path)
args=parser.parse_args()
receipt="JSON.parse(localStorage.getItem('ac8.selection.v1'))"


@contextmanager
def launch(pw,base,profile):
    with subprocess.Popen([base/'runtime/python.exe','-Bu','-X','utf8',base/'app/bridge.py'],
                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,encoding='utf8',
                          creationflags=subprocess.CREATE_NO_WINDOW) as worker:
        def audio(request):
            worker.stdin.write(json.dumps(request)+'\n')
            worker.stdin.flush()
            response=json.loads(worker.stdout.readline())
            if 'error' in response:raise ValueError(response['error'])
            return response['result']
        try:
            context=pw.chromium.launch_persistent_context(profile,channel='msedge',headless=True)
            try:
                page=context.pages[0]
                page.expose_function('testAudio',audio)
                page.add_init_script("window.__TAURI__={tauri:{invoke:async(op,args)=>op==='audio'?window.testAudio(args.request):false}}")
                page.goto((root/'desktop/ui/index.html').as_uri())
                page.wait_for_function("()=>!document.querySelector('#play').disabled && document.querySelector('#list .song')")
                audio({'op':'volume','value':0})
                yield page,audio
            finally:context.close()
        finally:
            worker.communicate('{"op":"quit"}\n',timeout=30)


def check_restore(page,audio,saved):
    assert page.evaluate(receipt)==saved
    state=audio({'op':'state'})
    assert state['song']==saved['song'] and state['arrangementId']==saved['arrangement']
    assert not state['playing'] and not state['wantsPlay']
    assert page.locator('#filter').input_value()==saved['tab']
    assert page.locator('#query').input_value()==saved['query']
    assert page.locator('#list .focused').get_attribute('data-id')==str(saved['focused'])


with tempfile.TemporaryDirectory() as temp, sync_playwright() as pw:
    folder=Path(temp)
    with ZipFile(args.archive) as archive:archive.extractall(folder)
    base=next(folder.glob('*/app/bridge.py')).parents[1]
    settings=base/'audio/settings.json'
    settings.parent.mkdir()
    settings.write_text(json.dumps({'game':str(args.game)}),encoding='utf8')
    profile=folder/'profile'
    with launch(pw,base,profile) as (page,audio):
        page.locator('#filter').select_option('单段配乐')
        page.locator('#query').fill('Briefing')
        rows=page.locator('#list .song')
        rows.nth(1).dblclick()
        rows.first.click()
        page.locator('#order').click()
        page.wait_for_function("()=>"+receipt+".libraryMode==='shuffle'")
        single=page.evaluate(receipt)
        assert single['focused']!=single['song'] and single['song'] in single['queue']
        assert single['query']=='Briefing' and single['entry'] is None
    with launch(pw,base,profile) as (page,audio):
        check_restore(page,audio,single)
        page.locator('#filter').select_option('互动配乐')
        page.locator('#query').fill('Hortus Regis Alti')
        page.locator('#list .song').first.dblclick()
        page.locator('#arrPreview').get_by_role('button',name='复制').click()
        page.locator('#arrEditor input[type=text]').fill('重启测试')
        page.get_by_role('button',name='保存并选用').click()
        page.wait_for_function("()=>document.querySelector('#arrList [aria-pressed=true]').dataset.arrangement.startsWith('custom-')")
        add=page.locator('#arrPreview').get_by_role('button',name='加入播放列表')
        add.click();add.click()
        page.locator('[data-arrangement=free]').click()
        page.locator('#filter').select_option('playlist')
        rows=page.locator('#list .song')
        rows.nth(1).dblclick()
        rows.first.click()
        page.wait_for_function("()=>"+receipt+".entry!==null")
        listed=page.evaluate(receipt)
        assert listed['entry']!=listed['focused'] and listed['arrangement'].startswith('custom-')
    with launch(pw,base,profile) as (page,audio):
        check_restore(page,audio,listed)
        assert page.locator('#list .current').get_attribute('data-id')==listed['entry']
        following=page.locator('#following')
        assert following.is_disabled() and page.locator('#previous').is_enabled()
        page.locator('#filter').select_option('单段配乐')
        page.locator('#query').fill('Briefing')
        page.locator('#list .song').first.click()
        browsing=page.evaluate(receipt)
        assert browsing['entry']==listed['entry'] and browsing['tab']=='单段配乐'
    with launch(pw,base,profile) as (page,audio):
        check_restore(page,audio,browsing)
        assert page.locator('#following').is_disabled()
        page.locator('#filter').select_option('playlist')
        page.locator('#list .current').click()
        page.locator('#removeEntry').click()
    with launch(pw,base,profile) as (page,audio):
        fallback=page.evaluate(receipt)
        assert fallback['entry'] is None and fallback['song']==listed['song']
        assert fallback['arrangement']==listed['arrangement'] and fallback['tab']=='playlist'
        assert len(fallback['queue'])>1
print('Passed five cold starts: selected row, filtered library queue, duplicate playlist entry, arrangement, independent browsing and deleted entry.')
