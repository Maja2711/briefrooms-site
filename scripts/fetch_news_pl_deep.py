#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Resilient PL Aktualności builder."""
from __future__ import annotations
import os,re,sys
from pathlib import Path
import fetch_news_pl_hybrid as hybrid
from comment_quality import QUALITY_STATUS,QUALITY_VERSION,validate_comment
from external_media_policy import external_image_url
from newsroom_articles import enrich_sections_with_homepage_quality,round_robin_section_items
from news_section_reserve import load_news_section_reserve
from newsroom_style import apply_newsroom_style
from news_publication_diagnostics import record_item
from news_story_dedupe import audit_html,assert_no_duplicate_stories,deduplicate_sections,load_recent_history,save_history
base=hybrid.base
HISTORY_PATH=Path(__file__).resolve().parents[1]/"data"/"news_story_history_pl.json"
_original_fetch=base.fetch_section
_original_render=base.render_html
_original_finalize_sections=base.finalize_sections
WEATHER_RE=re.compile(r"(tvnmeteo|pogoda|pogodowy|burza|burze|radar burz|mapa opad|opady|deszcz|ulewa|wiatr|wichura|grad|śnieg|mróz|upał|temperatura|prognoza|IMGW|meteop)",re.I)
TVN24_RE=re.compile(r"(TVN24|tvn24\.pl)",re.I)
SECTION_MAXIMUMS={key:bounds[1] for key,bounds in base.SECTION_PUBLISH_BOUNDS.items()}
MIN_TOTAL_ITEMS=sum(bounds[0] for bounds in base.SECTION_PUBLISH_BOUNDS.values())
SECTION_TABS_CSS="""
    html{scroll-behavior:smooth}
    .section-tabs{position:sticky;top:0;z-index:20;display:flex;gap:10px;justify-content:center;align-items:center;flex-wrap:wrap;margin:8px auto 18px;padding:10px 12px;background:rgba(8,15,30,.72);backdrop-filter:blur(14px);border:1px solid rgba(255,255,255,.10);border-radius:999px;box-shadow:0 10px 28px rgba(0,0,0,.28)}
    .section-tabs a{display:inline-flex;align-items:center;justify-content:center;min-width:112px;padding:9px 16px;border-radius:999px;color:#fdf3e3;text-decoration:none;font-weight:800;background:rgba(255,255,255,.06);border:1px solid rgba(255,255,255,.12)}
    .section-tabs .brand-link{min-width:auto;padding:8px 14px;background:linear-gradient(135deg,rgba(248,201,122,.30),rgba(255,255,255,.08));border-color:rgba(248,201,122,.46);color:#fff}
    .section-tabs .brand-mark{display:inline-flex;align-items:center;justify-content:center;width:34px;height:28px;border-radius:10px;color:#101827;background:linear-gradient(135deg,#087f9a,#23d5cc 42%,#d6fbff);font-weight:950;letter-spacing:-.08em}
    .section-tabs a:hover,.section-tabs a:focus-visible{background:rgba(248,201,122,.18);border-color:rgba(248,201,122,.42);color:#fff;outline:none;transform:translateY(-1px)}
    section.card{scroll-margin-top:92px}
    @media(max-width:640px){.section-tabs{border-radius:22px;justify-content:stretch}.section-tabs a{flex:1 1 30%;min-width:auto;padding:9px 10px;font-size:.92rem}.section-tabs .brand-link{flex:1 1 100%;justify-content:center}}
"""
SECTION_TABS_HTML='''<nav class="section-tabs" aria-label="Sekcje aktualności">
  <a class="brand-link" href="/pl/" aria-label="BRs — strona startowa"><span class="brand-mark">BRs</span></a>
  <a href="#polityka">Polityka</a><a href="#ekonomia">Ekonomia</a><a href="#zdrowie">Zdrowie</a><a href="#nauka">Nauka</a><a href="#sport">Sport</a>
</nav>'''
def _item_text(item): return " ".join(str(item.get(k,"") or "") for k in ("title","summary_raw","source_name","link"))
def _strict_comment(item):
    text=str(item.get("full_brief") or item.get("ai_summary") or ""); q=validate_comment(text,"pl")
    ok=q.valid and item.get("comment_quality_status")==QUALITY_STATUS and item.get("comment_quality_version")==QUALITY_VERSION and item.get("comment_generation_status")=="ai_review_approved" and item.get("summary_basis")=="article_text_ai_reviewed"
    return q.text if ok else ""
def _clear_comment(item):
    for k in ("full_brief","ai_summary","ai_why","ai_uncertain"): item[k]=""
    item["comment_generation_status"]="source_only"; item["summary_basis"]="source_only"
def fetch_section_strict(section_key,summarize=True):
    items=_original_fetch(section_key,summarize=summarize); kept=[]; tvn=0
    for item in items:
        text=_item_text(item)
        if WEATHER_RE.search(text) and section_key not in {"zdrowie","nauka"}: continue
        if TVN24_RE.search(text):
            if tvn>=1: continue
            tvn+=1
        if section_key=="polityka" and not str(item.get("thumbnail_url") or "").strip(): continue
        kept.append(item)
    return kept
def summarize_sections_pl_full(sections):
    unique,rejected=deduplicate_sections(sections,load_recent_history(HISTORY_PATH)); sections.clear(); sections.update(unique)
    if rejected: print(f"NEWS_CANDIDATE_DEDUPE_PL rejected={len(rejected)}")
    english=[item for _,item in round_robin_section_items(sections) if item.get("_source_was_english")]
    translations={}
    try: translations=base.summarize_news_items(items=english,lang="pl",cache=base.CACHE,post=base.requests.post)
    except Exception as exc: print(f"[WARN] PL title translation unavailable: {exc}",file=sys.stderr)
    for key,items in sections.items():
        out=[]
        for item in items:
            if item.get("_source_was_english"):
                tr=translations.get(str(item.get("_comment_batch_id") or ""),{})
                if not tr.get("title_pl"): continue
                item["title"]=str(tr["title_pl"]); item["source_name"]=hybrid.polish_source_label(item.get("source_name","Źródło"))
            out.append(item)
        sections[key]=out
    base.save_cache(base.AI_CACHE_PATH,base.CACHE)
    reserve=load_news_section_reserve("pl",image_lookup=base.article_image)
    for key,items in sections.items(): items.extend(reserve.get(key,[]))
    combined,rejected=deduplicate_sections(sections,load_recent_history(HISTORY_PATH)); sections.clear(); sections.update(combined)
    if rejected: print(f"NEWS_RESERVE_DEDUPE_PL rejected={len(rejected)}")
    try: enriched=enrich_sections_with_homepage_quality(sections,"pl",keep_unapproved=True)
    except Exception as exc: print(f"[WARN] PL AI enrichment unavailable; publishing source-only cards: {exc}",file=sys.stderr); return
    sections.clear(); sections.update(enriched)
def _rebalance(final):
    minimum=6
    donor_order={"zdrowie":["nauka","polityka","biznes","sport"],"nauka":["zdrowie","polityka","biznes","sport"],"biznes":["polityka","nauka","sport","zdrowie"],"polityka":["biznes","nauka","sport","zdrowie"],"sport":["polityka","biznes","nauka","zdrowie"]}
    for target in final:
        while len(final[target])<minimum:
            moved=False
            for donor in donor_order.get(target,[]):
                if donor in final and len(final[donor])>minimum:
                    item=final[donor].pop()
                    item["section_rebalanced_from"]=donor
                    final[target].append(item); moved=True; break
            if not moved: break
    return final
def finalize_sections_strict(sections):
    sections=_original_finalize_sections(sections); final={}; comments=source_only=0
    for key,items in sections.items():
        publishable=[]
        for item in items:
            thumb=external_image_url(item.get("thumbnail_url"),item.get("link"))
            if not thumb or not str(item.get("title") or "").strip() or not str(item.get("link") or "").strip(): continue
            item["thumbnail_url"]=thumb; comment=_strict_comment(item)
            if comment:
                item["full_brief"]=comment; item["ai_summary"]=comment; item["ai_why"]=""; item["ai_uncertain"]=""; comments+=1
            else: _clear_comment(item); source_only+=1
            publishable.append(item)
        final[key]=publishable[:SECTION_MAXIMUMS.get(key,10)]
    final,rejected=deduplicate_sections(final,load_recent_history(HISTORY_PATH)); final=_rebalance(final)
    if rejected:
        print(f"NEWS_QUALITY_DEDUPE_PL rejected={len(rejected)} details={rejected}")
        for item in rejected: record_item("pl",item.get("_diagnostic_feed_url",""),"rejected_duplicate",published_at=item.get("_diagnostic_published_at",""),pipeline="news")
    for items in final.values():
        for item in items: record_item("pl",item.get("_diagnostic_feed_url",""),"accepted",published_at=item.get("_diagnostic_published_at",""),pipeline="news")
    print(f"NEWS_GRACEFUL_DEGRADATION_PL comments={comments} source_only={source_only} sections={ {k:len(v) for k,v in final.items()} }")
    return final
def _add_section_tabs(html):
    if 'class="section-tabs"' not in html:
        html=html.replace("</style>",SECTION_TABS_CSS+"\n  </style>",1).replace("<main>\n","<main>\n"+SECTION_TABS_HTML+"\n",1)
    for title,sid in {"Polityka / Kraj":"polityka","Ekonomia / Biznes":"ekonomia","Zdrowie":"zdrowie","Nauka":"nauka","Sport":"sport"}.items(): html=html.replace(f'<section class="card">\n  <h2>{title}</h2>',f'<section class="card" id="{sid}">\n  <h2>{title}</h2>',1)
    return html
def _remove_empty_comment_blocks(html):
    return re.sub(r'\s*<div class="ai-note">\s*<div class="ai-head">[\s\S]*?</div>\s*<div class="sec"><strong>Najważniejsze:</strong>\s*</div>\s*(?:<div class="sec"><strong>Dlaczego to ważne:</strong>\s*</div>\s*)?</div>',"",html,flags=re.I)
def render_html_strict(sections):
    items=[i for values in sections.values() for i in values]; under={k:len(v) for k,v in sections.items() if len(v)<base.SECTION_PUBLISH_BOUNDS[k][0]}
    if under: raise RuntimeError("PL news publication kept on last-good version: each section requires at least 6 source-linked photo items; "+", ".join(f"{k}={v}" for k,v in under.items()))
    if len(items)<MIN_TOTAL_ITEMS: raise RuntimeError(f"PL news publication kept on last-good version: only {len(items)} source-linked photo items")
    for item in items:
        if not str(item.get("thumbnail_url") or "").strip(): raise RuntimeError(f"PL news publication blocked by missing source thumbnail: {item.get('title','')[:80]}")
        comment=str(item.get("full_brief") or item.get("ai_summary") or "").strip()
        if comment and not validate_comment(comment,"pl").valid: raise RuntimeError(f"PL news publication blocked by invalid visible comment: {item.get('title','')[:80]}")
    assert_no_duplicate_stories(sections); html=_original_render(sections); html=_remove_empty_comment_blocks(html)
    html=re.sub(r'\n\s*<div class="sec"><strong>Dlaczego to ważne:</strong>.*?</div>',"",html,flags=re.I|re.S).replace("<strong>Najważniejsze:</strong> ","")
    html=_add_section_tabs(html); audit_html(html)
    if os.environ.get("BR_NEWS_PERSIST_HISTORY")=="1": save_history(sections,HISTORY_PATH)
    return apply_newsroom_style(html,"pl")
base.fetch_section=fetch_section_strict
base.summarize_sections_pl=summarize_sections_pl_full
base.finalize_sections=finalize_sections_strict
base.render_html=render_html_strict
if __name__=="__main__": base.main()
