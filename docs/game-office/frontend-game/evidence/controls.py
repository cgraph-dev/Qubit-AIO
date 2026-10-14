import hashlib
import json
import os
from pathlib import Path
from playwright.sync_api import sync_playwright

out=Path(os.environ['TMPDIR'])/'office-audit-live'
with sync_playwright() as p:
    browser=p.chromium.launch(executable_path='/usr/bin/google-chrome',headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
    page=browser.new_page(viewport={'width':1600,'height':1000},device_scale_factor=2)
    page.goto('http://127.0.0.1:8890/qubit-office')
    page.wait_for_selector('[data-state="ready"]')
    c=page.locator('[data-state="ready"] canvas').first
    def pixels():return hashlib.sha256(c.evaluate('(c)=>c.toDataURL()').encode()).hexdigest()
    before=pixels()
    page.get_by_role('button',name='Zoom',exact=True).click()
    zoom=pixels()
    page.get_by_role('button',name='Fit',exact=True).click()
    fit=pixels()
    page.get_by_role('button',name='Reset',exact=True).click()
    reset=pixels()
    result={'zoom_button_changes_pixels':zoom!=before,'fit_restores_initial_pixels':fit==before,'fit_changes_zoomed_pixels':fit!=zoom,'reset_restores_initial_pixels':reset==before,'dpr_geometry':c.evaluate('(c)=>({dpr:devicePixelRatio,backingWidth:c.width,cssWidth:c.getBoundingClientRect().width,backingHeight:c.height,cssHeight:c.getBoundingClientRect().height})')}
    page.set_viewport_size({'width':390,'height':844})
    page.wait_for_timeout(650)
    result['mobile_canvas']=c.bounding_box()
    result['mobile_scroll_height']=page.evaluate('document.documentElement.scrollHeight')
    # Count non-background minimap pixels rather than interpreting an image-analysis model's prose.
    result['minimap_pixels']=page.locator('[data-state="ready"] canvas').nth(1).evaluate('''(c)=>{const d=c.getContext('2d').getImageData(0,0,c.width,c.height).data;let n=0;for(let i=0;i<d.length;i+=4)if(d[i]!==20||d[i+1]!==26||d[i+2]!==35)n++;return {nonBackground:n,total:c.width*c.height};}''')
    (out/'controls.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    browser.close()
