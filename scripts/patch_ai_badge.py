#!/usr/bin/env python3
from pathlib import Path

FILES = [Path('pl/index.html'), Path('en/index.html')]

SCRIPT = '''
<script>
(function(){
  function setupAiBadge(){
    document.querySelectorAll('.ai-badge').forEach(function(badge){
      if(badge.dataset.aiToggleReady==='1')return;
      var text=badge.querySelector('.ai-text') || badge.querySelector('span:last-child');
      if(!text)return;
      badge.dataset.aiToggleReady='1';
      badge.dataset.expanded='0';
      badge.setAttribute('role','button');
      badge.setAttribute('tabindex','0');
      badge.setAttribute('aria-label','AI-assisted');
      badge.style.cursor='pointer';
      text.classList.add('ai-text');
      text.textContent='AI';
      function toggle(){
        var expanded=badge.dataset.expanded==='1';
        badge.dataset.expanded=expanded?'0':'1';
        text.textContent=expanded?'AI':'AI-assisted';
      }
      badge.addEventListener('click',function(e){e.preventDefault();e.stopPropagation();toggle();});
      badge.addEventListener('keydown',function(e){if(e.key==='Enter'||e.key===' '){e.preventDefault();toggle();}});
    });
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',setupAiBadge);else setupAiBadge();
})();
</script>
'''

for path in FILES:
    html = path.read_text(encoding='utf-8')
    original = html
    html = html.replace('<span class="ai-dot"></span><span>AI-assisted</span>', '<span class="ai-dot"></span><span class="ai-text">AI</span>')
    html = html.replace("<span class=\"ai-dot\"></span><span>Wspomagane przez AI</span>", "<span class=\"ai-dot\"></span><span class=\"ai-text\">AI</span>")
    if 'dataset.aiToggleReady' not in html:
        html = html.replace('</body>', SCRIPT + '\n</body>')
    if html != original:
        path.write_text(html, encoding='utf-8')
        print(f'patched {path}')
    else:
        print(f'no change needed {path}')
