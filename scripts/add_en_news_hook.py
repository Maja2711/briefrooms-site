from pathlib import Path

p = Path('en/news.html')
s = p.read_text(encoding='utf-8')
if 'en-news-thumbs.js' not in s:
    q = chr(34)
    tag = chr(60) + 'script src=' + q + '/scripts/en-news-thumbs.js?v=2' + q + ' defer>' + chr(60) + '/script>'
    p.write_text(s.replace(chr(60) + '/body>', tag + '\n' + chr(60) + '/body>'), encoding='utf-8', newline='\n')
