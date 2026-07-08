#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Patch homepage renderers so Pilne/Breaking is never printed on photo badges."""
from pathlib import Path

FILES = [Path('pl/index.html'), Path('en/index.html')]
PATCH = r"""
<script>
(function(){
  function hide(){
    document.querySelectorAll('.brief-card .tag').forEach(function(el){
      var t=(el.textContent||'').trim().toLowerCase();
      if(t==='pilne'||t==='breaking'||t==='urgent'||t==='alert') el.remove();
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',hide);else hide();
  setInterval(hide,250);
})();
</script>
""".strip()

for path in FILES:
    text = path.read_text(encoding='utf-8')
    if "brief-card .tag" not in text:
        text = text.replace('</body>', PATCH + '\n</body>')
        path.write_text(text, encoding='utf-8', newline='\n')
