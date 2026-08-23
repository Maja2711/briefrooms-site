from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from html.parser import HTMLParser
from typing import Any, Dict, List, Mapping, Optional, Tuple

from gse_discovery_common import (
    CANDIDATE_VERSION, MAX_DETAIL_FETCHES, MIN_VERIFY_SCORE, REQUEST_SLEEP_SECONDS,
    clamp, classify_scenarios, detect_actor_buckets, host_is_official,
    infer_features, iso_z, normalize_text, parse_time, stable_id, strip_tags,
    title_overlap,
)

@dataclass(frozen=True)
class LinkRecord:
    url: str
    title: str
    nearby_text: str

class AnchorParser(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self.base_url=base_url; self._href=None; self._parts=[]; self.records=[]; self._window=[]
    def handle_starttag(self, tag, attrs):
        if tag.lower()=="a":
            href=dict(attrs).get("href")
            if href: self._href=urllib.parse.urljoin(self.base_url,href); self._parts=[]
    def handle_data(self, data):
        text=re.sub(r"\s+"," ",data).strip()
        if not text: return
        self._window=(self._window+[text])[-30:]
        if self._href is not None: self._parts.append(text)
    def handle_endtag(self, tag):
        if tag.lower()=="a" and self._href is not None:
            title=re.sub(r"\s+"," "," ".join(self._parts)).strip()
            if title: self.records.append(LinkRecord(self._href,title," ".join(self._window[-16:])))
            self._href=None; self._parts=[]

class HttpClient:
    def __init__(self, timeout=25, retries=3, sleep_seconds=REQUEST_SLEEP_SECONDS):
        self.timeout=timeout; self.retries=retries; self.sleep_seconds=sleep_seconds
        self.user_agent="BriefRooms-GSE-HistoricalDiscovery/1.0 research https://briefrooms.com"
    def get_text(self,url):
        last=None
        for attempt in range(self.retries):
            try:
                req=urllib.request.Request(url,headers={"User-Agent":self.user_agent,"Accept":"text/html,application/xhtml+xml"})
                with urllib.request.urlopen(req,timeout=self.timeout) as r: text=r.read().decode("utf-8","replace")
                if self.sleep_seconds: time.sleep(self.sleep_seconds)
                return text
            except Exception as exc:
                last=exc; time.sleep(min(2.0,.4*(attempt+1)))
        raise RuntimeError(f"GET failed: {url}: {last}")

def extract_page_date(page):
    patterns=(r'<time[^>]+datetime=["\']([^"\']+)["\']',r'"datePublished"\s*:\s*"([^"]+)"',r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2})\b',r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+20\d{2})\b')
    for p in patterns:
        m=re.search(p,page,flags=re.I|re.S)
        if m:
            try: return parse_time(m.group(1).replace("Sept.","Sep"))
            except Exception: pass
    return None

def extract_page_title(page):
    for p in (r'<h1[^>]*>(.*?)</h1>',r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\'](.*?)["\']',r'<title[^>]*>(.*?)</title>'):
        m=re.search(p,page,flags=re.I|re.S)
        if m:
            value=strip_tags(m.group(1))
            if value: return value
    return ""

def date_from_nearby(text):
    for p in (r'\b((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2})\b',r'\b((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2},\s+20\d{2})\b',r'\b(20\d{2}-\d{2}-\d{2})\b'):
        m=re.search(p,text,flags=re.I)
        if m:
            try: return parse_time(m.group(1).replace("Sept.","Sep"))
            except Exception: pass
    return None

def source_link_allowed(kind,url):
    path=urllib.parse.urlparse(url).path.lower()
    if kind=="ofac_recent_actions": return "/recent-actions/" in path and path.rstrip("/") not in {"/recent-actions","/recent-actions/sanctions-list-updates"}
    if kind=="treasury_press_releases": return "/news/press-releases/" in path and path.rstrip("/")!="/news/press-releases"
    if kind=="defense_search": return "/news/releases/" in path or "/news/news-stories/article/" in path
    return host_is_official(url)

def format_list_url(source,page,query):
    return str(source["list_url"]).format(page=page,query=urllib.parse.quote(query or "",safe=""),query_path=urllib.parse.quote(query or "",safe=""))

def discover_source_documents(source,client,*,start_at,end_at):
    kind=str(source["kind"]); name=str(source["name"]); queries=list(source.get("queries") or [None]); max_pages=int(source.get("max_pages") or 50); stop=int(source.get("empty_page_stop") or 2); reliability=clamp(float(source.get("source_reliability") or .9))
    rows={}; errors=[]; fetched=0
    for query in queries:
        no_new=0; oldest=None
        for page in range(max_pages):
            url=format_list_url(source,page,query)
            try: page_text=client.get_text(url)
            except Exception as exc:
                errors.append(f"{url}: {exc}"); no_new+=1
                if no_new>=stop: break
                continue
            fetched+=1; parser=AnchorParser(url); parser.feed(page_text); new=0; dates=[]
            for link in parser.records:
                if not host_is_official(link.url) or not source_link_allowed(kind,link.url): continue
                scenarios=classify_scenarios(link.title)
                if not scenarios: continue
                at=date_from_nearby(link.nearby_text)
                if at is not None:
                    dates.append(at)
                    if at<start_at-timedelta(days=7) or at>end_at+timedelta(days=7): continue
                key=link.url.split("#",1)[0]
                candidate={"schema_version":CANDIDATE_VERSION,"candidate_id":stable_id("gse-historical-doc",key),"source_name":name,"source_kind":kind,"source_ref":key,"source_reliability":reliability,"list_title":link.title,"list_date":iso_z(at) if at else None,"query":query,"preclassification":scenarios,"discovery_verified":False,"market_outcomes_used":False}
                if key not in rows: rows[key]=candidate; new+=1
            if dates: oldest=min(dates) if oldest is None else min(oldest,min(dates))
            no_new=0 if new else no_new+1
            if oldest is not None and oldest<start_at-timedelta(days=31) and no_new>=1: break
            if no_new>=stop: break
    return list(rows.values()),{"source_name":name,"kind":kind,"list_pages_fetched":fetched,"prefiltered_documents":len(rows),"errors":errors[:20]}

def verify_document(candidate,client,*,start_at,end_at):
    url=str(candidate["source_ref"])
    if not host_is_official(url): return None
    try: page=client.get_text(url)
    except Exception: return None
    page_title=extract_page_title(page) or str(candidate.get("list_title") or "")
    page_date=extract_page_date(page); list_date=None
    if candidate.get("list_date"):
        try: list_date=parse_time(candidate["list_date"])
        except Exception: pass
    event_at=page_date or list_date
    if event_at is None or event_at<start_at or event_at>end_at+timedelta(days=1): return None
    body=strip_tags(page)[:30000]; scenarios=classify_scenarios(page_title,body)
    if not scenarios: return None
    overlap=title_overlap(str(candidate.get("list_title") or ""),page_title)
    title_ok=overlap>=.35 or normalize_text(str(candidate.get("list_title") or "")) in normalize_text(page_title)
    date_ok=not(page_date and list_date) or abs((page_date-list_date).days)<=10
    score=clamp(.34+.22*max(float(r["score"]) for r in scenarios)+(.16 if title_ok else .03)+(.12 if date_ok else 0)+.12*float(candidate.get("source_reliability") or 0))
    if score<MIN_VERIFY_SCORE: return None
    row=dict(candidate); row.update({"title":page_title,"event_at":iso_z(event_at),"scenario_scores":scenarios,"scenario_types":[str(x["scenario_type"]) for x in scenarios],"actor_buckets":detect_actor_buckets(f"{page_title} {body[:6000]}"),"features":infer_features(page_title,body,scenarios),"verification_score":round(score,6),"verification_status":"machine_verified_primary_source","title_confirmed":bool(title_ok),"date_confirmed":bool(date_ok),"page_sha256":hashlib.sha256(page.encode("utf-8","replace")).hexdigest(),"discovery_verified":True,"market_outcomes_used":False})
    return row
