#!/usr/bin/env python3
from __future__ import annotations
import json,re
from pathlib import Path
P=Path('en/home_brief.json')
BAD=[
 'BBC Homepage','Skip to content','Accessibility Help','Your account',
 'Home News Sport Earth Reel Worklife Travel Culture Future Music',
 'TV Weather Sounds','More menu','Search BBC','Image source,','Image caption,','external'
]

def clean(t:str)->str:
    t=str(t or '')
    t=t.replace('Â£','£').replace('Â','').replace('â','–').replace('â','—').replace('â',"'").replace('â','"').replace('â','"').replace('Ã¨','è')
    for marker in ('TV Weather Sounds...','TV Weather Sounds…','TV Weather Sounds'):
        i=t.find(marker)
        if i>=0:
            t=t[i+len(marker):]
            break
    for x in BAD:
        t=t.replace(x,' ')
    t=re.sub(r'\s+',' ',t).strip(' -–—·•/\t\n\r')
    return t

def simple(text:str,title:str)->str:
    text=clean(text)
    parts=[clean(x) for x in re.findall(r'[^.!?…]+[.!?…]+|[^.!?…]+$',text)]
    parts=[x for x in parts if len(x)>35 and not any(y in x.lower() for y in ('homepage','accessibility','worklife','search bbc','more menu'))]
    out=' '.join(parts[:2])
    if not out and title:
        out=f'This article is about: {title.rstrip(".")}.'
    if len(out)>260:
        out=out[:260].rsplit(' ',1)[0]+'…'
    return out

def main():
    data=json.loads(P.read_text(encoding='utf-8'))
    for item in data.get('latest',[]):
        title=str(item.get('title') or '')
        item['summary']=simple(item.get('details') or item.get('summary') or title,title)
        item['details']=simple(item.get('details') or item.get('summary') or title,title)
    data['quality_mode']='simple-clear-comments-auto'
    P.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Cleaned EN homepage brief comments')
if __name__=='__main__': main()
