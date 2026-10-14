import hashlib
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

out = Path(os.environ['TMPDIR']) / 'office-audit-live'
out.mkdir(exist_ok=True)
with sync_playwright() as p:
    browser = p.chromium.launch(executable_path='/usr/bin/google-chrome', headless=True, args=['--no-sandbox','--disable-dev-shm-usage'])
    page = browser.new_page(viewport={'width':1600,'height':1000})
    errors, bad, payloads = [], [], []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.on('response', lambda r: bad.append({'status':r.status,'url':r.url}) if r.status >= 400 else None)
    def response(r):
        if '/api/office/tower' in r.url and r.status == 200:
            try: payloads.append(r.json())
            except Exception: pass
    page.on('response', response)
    page.add_init_script('''window.__officeAuditListeners=[];
      const add=EventTarget.prototype.addEventListener;
      EventTarget.prototype.addEventListener=function(type,fn,opts){
        if(this.tagName==='CANVAS')window.__officeAuditListeners.push(type);
        return add.call(this,type,fn,opts);
      };''')
    page.goto('http://127.0.0.1:8890/qubit-office', wait_until='domcontentloaded')
    page.wait_for_selector('[data-state="ready"]',timeout=30000)
    page.screenshot(path=str(out/'initial.png'))
    assert payloads, 'No real tower payload captured'
    payload = payloads[-1]
    (out/'tower.json').write_text(json.dumps(payload,indent=2))
    # Freeze further snapshot responses to the actual received payload, not synthetic office data.
    page.route('**/api/office/tower*', lambda route: route.fulfill(status=200,json=payload))
    canvas=page.locator('[data-state="ready"] canvas').first
    box=canvas.bounding_box()
    def pixels():return hashlib.sha256(canvas.evaluate('(c)=>c.toDataURL()').encode()).hexdigest()
    before=pixels()
    page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
    page.mouse.wheel(0,-320)
    page.wait_for_timeout(120)
    wheel_changed=pixels()!=before
    before=pixels()
    page.mouse.move(box['x']+box['width']/2,box['y']+box['height']/2)
    page.mouse.down();page.mouse.move(box['x']+box['width']/2+90,box['y']+box['height']/2+50,steps=5);page.mouse.up()
    page.wait_for_timeout(120)
    drag_changed=pixels()!=before
    initial_buttons=page.locator('[data-state="ready"] button').all_text_contents()
    page.get_by_role('button',name='F1 · executive',exact=True).click()
    page.wait_for_timeout(120)
    floor_changed=pixels()!=before
    page.screenshot(path=str(out/'floor1.png'))
    page.get_by_role('button',name='P&L',exact=True).first.click()
    page.wait_for_timeout(120)
    pnl_present='P&L by day' in page.locator('body').inner_text()
    loaded=page.evaluate('window.__qubit_loaded__ || []')
    atlas=page.evaluate('({ready:window.QubitAtlas?.ready?.(),gaps:window.QubitAtlas?.gaps?.()})')
    result={'url':page.url,'errors':errors,'failed_requests':bad,'modules':loaded,'canvas_event_listeners':page.evaluate('window.__officeAuditListeners'), 'wheel_changed_pixels':wheel_changed,'drag_changed_pixels':drag_changed,'floor_button_changed_pixels':floor_changed,'pnl_button_opens_chart':pnl_present,'buttons':initial_buttons,'atlas':atlas,'tower_keys':list(payload),'character_keys':list(payload.get('characters',[{}])[0]),'floors':len(payload.get('floorplan',{}).get('floors',[])),'characters':len(payload.get('characters',[])),'initial_viewport':{'width':1600,'height':1000},'canvas_box':box}
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(120)
    result['mobile_overflow']=page.evaluate('({viewport:innerWidth,body:document.body.scrollWidth,doc:document.documentElement.scrollWidth})')
    page.screenshot(path=str(out/'mobile.png'))
    (out/'probe.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    browser.close()
