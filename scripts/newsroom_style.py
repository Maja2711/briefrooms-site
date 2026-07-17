#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Apply the homepage card language to generated PL and EN news pages."""

from __future__ import annotations

import re

NEWSROOM_CSS = r"""
    body[data-page="news"]{
      margin:0!important;
      color:#eef7ff!important;
      font-family:Inter,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif!important;
      background:
        radial-gradient(1000px 520px at 12% -10%,rgba(46,214,201,.18),transparent 58%),
        radial-gradient(900px 620px at 86% 16%,rgba(73,168,255,.13),transparent 58%),
        linear-gradient(180deg,#06131f 0%,#071827 48%,#081522 100%)!important;
      min-height:100vh;
    }
    body[data-page="news"]:before{
      content:"";position:fixed;inset:0;pointer-events:none;
      background-image:linear-gradient(rgba(255,255,255,.025) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.02) 1px,transparent 1px);
      background-size:44px 44px;
      mask-image:linear-gradient(180deg,rgba(0,0,0,.86),transparent 84%);
    }
    body[data-page="news"] header{
      position:relative;z-index:1;max-width:1360px!important;margin:0 auto!important;
      padding:30px 24px 12px!important;text-align:left!important;
    }
    body[data-page="news"] header h1{
      margin:0 0 8px!important;font-size:clamp(38px,5vw,58px)!important;
      line-height:1.03!important;letter-spacing:-.045em!important;
    }
    body[data-page="news"] header .sub{margin:0!important;color:#9fb2c8!important;font-size:14px!important}
    body[data-page="news"] main{
      position:relative;z-index:1;max-width:1360px!important;margin:0 auto!important;
      padding:0 24px 64px!important;
    }
    body[data-page="news"] .section-tabs{
      top:10px!important;margin:18px auto 30px!important;max-width:1180px!important;
      background:rgba(6,19,31,.86)!important;border-color:rgba(255,255,255,.12)!important;
      box-shadow:0 18px 44px rgba(0,0,0,.26)!important;
    }
    body[data-page="news"] section.card{
      background:transparent!important;border:0!important;border-radius:0!important;
      box-shadow:none!important;padding:10px 0 24px!important;margin:18px 0 26px!important;
    }
    body[data-page="news"] section.card h2{
      margin:0 0 18px!important;padding:0 0 14px!important;
      color:#eef7ff!important;font-size:clamp(25px,3vw,34px)!important;
      line-height:1.08!important;letter-spacing:-.035em!important;
      border-bottom:1px solid rgba(255,255,255,.12)!important;
    }
    body[data-page="news"] ul.news{
      list-style:none!important;margin:0!important;padding:0!important;
      display:grid!important;grid-template-columns:repeat(2,minmax(0,1fr))!important;
      gap:18px!important;
    }
    body[data-page="news"] ul.news>li{
      min-width:0;margin:0!important;padding:0!important;overflow:hidden!important;
      border:1px solid rgba(255,255,255,.12)!important;border-radius:22px!important;
      background:linear-gradient(180deg,rgba(255,255,255,.105),rgba(255,255,255,.04))!important;
      box-shadow:0 18px 44px rgba(0,0,0,.20),inset 0 1px 0 rgba(255,255,255,.08)!important;
      transition:transform .2s ease,border-color .2s ease!important;
      display:flex!important;flex-direction:column!important;
    }
    body[data-page="news"] ul.news>li:hover{
      transform:translateY(-3px)!important;border-color:rgba(56,214,201,.32)!important;
    }
    body[data-page="news"] .news-main-link,
    body[data-page="news"] ul.news>li>a{
      display:block!important;color:inherit!important;text-decoration:none!important;
      min-width:0!important;
    }
    body[data-page="news"] ul.news>li>a::after{content:none!important;display:none!important}
    body[data-page="news"] .news-thumb{
      display:block!important;width:100%!important;min-width:0!important;height:190px!important;
      margin:0!important;padding:0!important;border:0!important;border-radius:0!important;
      overflow:hidden!important;background:radial-gradient(circle at 25% 15%,rgba(56,214,201,.32),transparent 40%),linear-gradient(135deg,#0d344a,#081827)!important;
      box-shadow:none!important;
    }
    body[data-page="news"] .news-thumb.has-image img{
      display:block!important;width:100%!important;height:100%!important;object-fit:cover!important;
    }
    body[data-page="news"] .news-thumb:not(.has-image){
      display:grid!important;place-items:center!important;text-align:center!important;
    }
    body[data-page="news"] .news-thumb:not(.has-image) .dot{display:none!important}
    body[data-page="news"] .news-thumb:not(.has-image) .title{
      display:block!important;max-width:80%!important;color:rgba(255,255,255,.82)!important;
      font-size:24px!important;font-weight:950!important;line-height:1.05!important;
      white-space:normal!important;
    }
    body[data-page="news"] .news-thumb:not(.has-image) .sub{display:none!important}
    body[data-page="news"] .news-title-wrap{display:block!important;padding:16px 16px 12px!important}
    body[data-page="news"] .news-text{
      display:block!important;padding:16px 16px 7px!important;
      color:#eef7ff!important;font-size:18px!important;font-weight:850!important;
      line-height:1.2!important;letter-spacing:-.015em!important;
    }
    body[data-page="news"] .news-title-wrap .news-text{padding:0!important}
    body[data-page="news"] .source-line{
      margin:0!important;padding:0 16px 12px!important;color:#88a0b6!important;
      font-size:12px!important;line-height:1.35!important;
    }
    body[data-page="news"] .news-title-wrap .source-line{padding:8px 0 0!important}
    body[data-page="news"] .ai-note{
      margin:0!important;padding:15px 16px 18px!important;border:0!important;
      border-top:1px solid rgba(255,255,255,.09)!important;border-radius:0!important;
      background:transparent!important;color:#b8c9d8!important;font-size:14px!important;
      line-height:1.55!important;flex:1!important;
    }
    body[data-page="news"] .ai-head{margin:0 0 9px!important}
    body[data-page="news"] .ai-badge{
      display:inline-flex!important;align-items:center!important;gap:7px!important;
      padding:5px 9px!important;border-radius:999px!important;
      background:rgba(56,214,201,.12)!important;border:1px solid rgba(56,214,201,.24)!important;
      color:#8ffff6!important;font-size:10px!important;font-weight:950!important;
      letter-spacing:.045em!important;text-transform:uppercase!important;
    }
    body[data-page="news"] .ai-dot{background:#38d6c9!important;box-shadow:0 0 8px rgba(56,214,201,.68)!important}
    body[data-page="news"] .sec{margin:0!important}
    body[data-page="news"] .sec strong{display:none!important}
    body[data-page="news"] .empty-state{
      grid-column:1/-1;padding:28px!important;border:1px solid rgba(255,255,255,.10)!important;
      border-radius:18px!important;background:rgba(255,255,255,.035)!important;
    }
    body[data-page="news"] footer{position:relative;z-index:1;color:#7f93a8!important}
    @media(max-width:760px){
      body[data-page="news"] header{padding:24px 14px 10px!important}
      body[data-page="news"] main{padding:0 14px 44px!important}
      body[data-page="news"] ul.news{grid-template-columns:1fr!important;gap:15px!important}
      body[data-page="news"] .news-thumb{height:178px!important}
      body[data-page="news"] .news-text{font-size:17px!important}
    }
"""


def apply_newsroom_style(html: str, lang: str) -> str:
    if "briefrooms-newsroom-v2" not in html:
        html = html.replace("</style>", NEWSROOM_CSS + "\n  /* briefrooms-newsroom-v2 */\n  </style>", 1)
    if lang == "en":
        html = html.replace("BriefRooms • AI comment", "Comment")
        html = html.replace("BriefRooms • AI Comment", "Comment")
    else:
        html = html.replace("BriefRooms • komentarz AI", "Komentarz")
    html = re.sub(r"[ \t]+(?=\n)", "", html)
    return html
