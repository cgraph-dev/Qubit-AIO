#!/usr/bin/env python
import json, re, urllib.request
from playwright.sync_api import sync_playwright
BASE="http://127.0.0.1:8890"
body=urllib.request.urlopen(BASE+"/",timeout=5).read().decode("utf-8","replace")
tok=re.search(r'window\.__QUBIT_SESSION_TOKEN__="([^"]+)"',body).group(1)
JS=r"""
() => {
  const el = document.querySelector('.settings-group');
  if(!el) return {none:true};
  const cs=getComputedStyle(el);
  const root=(document.querySelector('.qb-upstream'));
  const rootcs=root?getComputedStyle(root):null;
  return {
    className: el.className,
    borderTop: cs.borderTopWidth+' '+cs.borderTopStyle+' '+cs.borderTopColor,
    bg: cs.backgroundColor,
    card: cs.getPropertyValue('--card'),
    borderVar: cs.getPropertyValue('--border'),
    midgroundBase: cs.getPropertyValue('--midground-base'),
    hasQbUpstream: !!root,
    rootBorderVar: rootcs?rootcs.getPropertyValue('--border'):null,
  };
}
"""
with sync_playwright() as pw:
    b=pw.chromium.launch(headless=True)
    ctx=b.new_context(viewport={"width":1440,"height":950}, extra_http_headers={"X-Hermes-Session-Token":tok})
    p=ctx.new_page()
    for route in ["/qubit-scheduler","/nodes","/checkpoints"]:
        p.goto(BASE+route, wait_until="networkidle", timeout=30000)
        p.wait_for_timeout(1500)
        print(route, json.dumps(p.evaluate(JS)))
    b.close()
