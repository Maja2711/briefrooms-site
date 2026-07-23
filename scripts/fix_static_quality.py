#!/usr/bin/env python3
"""Static quality guard for generated BriefRooms pages.

Runs after manual fixes or automated generators. It removes generic AI boilerplate
from already-generated news HTML, preserves generator-owned static homepage cards,
keeps Hot X rendered only by the exact-post renderer, and preserves the Polish
editorial YouTube recommendations below Hot X.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
HOT_X_SCRIPT = '<script src="/scripts/hot-x-render.js?v=circulating-x-5" defer></script>'
HOT_X_RENDERER_RE = re.compile(
    r'\s*<script\s+src=["\']/scripts/hot-x-render\.js\?v=[^"\']+["\']\s+defer></script>\s*',
    re.I,
)

YOUTUBE_PICKS_STYLE = """<style id="br-youtube-picks-style">
.youtube-picks{margin-top:24px;padding-top:22px;border-top:1px solid rgba(255,255,255,.12)}
.youtube-picks__head{display:flex;align-items:flex-start;gap:11px;margin-bottom:12px}
.youtube-picks__icon{display:grid;place-items:center;width:32px;height:32px;flex:0 0 32px;border:1px solid rgba(255,102,102,.34);border-radius:10px;background:linear-gradient(145deg,rgba(255,90,90,.24),rgba(120,20,30,.18));color:#ff8c91;font-size:13px;box-shadow:inset 0 1px 0 rgba(255,255,255,.16)}
.youtube-picks__head h3{margin:0 0 4px;color:#eef7ff;font-size:18px;line-height:1.15}
.youtube-picks__head p{margin:0;color:#8fa6ba;font-size:12px;line-height:1.4}
.youtube-picks__grid{display:grid;gap:10px}
.youtube-pick{display:flex;min-width:0;flex-direction:column;gap:5px;padding:14px 15px;border:1px solid rgba(149,216,255,.14);border-radius:16px;background:linear-gradient(145deg,rgba(36,79,108,.34),rgba(11,34,52,.42));box-shadow:inset 0 1px 0 rgba(231,249,255,.08),0 12px 28px rgba(0,0,0,.16);text-decoration:none;transition:transform .18s ease,border-color .18s ease,background .18s ease}
.youtube-pick:hover{transform:translateY(-2px);border-color:rgba(111,215,224,.34);background:linear-gradient(145deg,rgba(42,91,123,.42),rgba(13,40,61,.48));text-decoration:none}
.youtube-pick__label{color:#edf8ff!important;font-size:14px!important;font-weight:900!important;line-height:1.3!important}
.youtube-pick__meta{color:#9eb3c5!important;font-size:11px!important;font-weight:600!important;line-height:1.45!important}
.youtube-pick strong{margin-top:3px;color:#71dfe5;font-size:12px}
.youtube-picks__note{margin:11px 2px 0!important;color:#6f879b!important;font-size:10px!important;line-height:1.4!important}
@media(prefers-reduced-motion:reduce){.youtube-pick{transition:none}}
</style>"""

YOUTUBE_PICKS_BLOCK = """<section class="youtube-picks" aria-labelledby="youtube-picks-title">
  <div class="youtube-picks__head">
    <span class="youtube-picks__icon" aria-hidden="true">▶</span>
    <div><h3 id="youtube-picks-title">Polecane na YouTube</h3><p>Wybrane kanały publicystyczne i analityczne.</p></div>
  </div>
  <div class="youtube-picks__grid">
    <a class="youtube-pick" href="https://www.youtube.com/@kotwarty" target="_blank" rel="noopener noreferrer">
      <span class="youtube-pick__label">Kanał Otwarty</span>
      <span class="youtube-pick__meta">Rozmowy, publicystyka i analiza bieżących wydarzeń.</span>
      <strong>Otwórz kanał →</strong>
    </a>
    <a class="youtube-pick" href="https://www.youtube.com/@KanalZeroPL" target="_blank" rel="noopener noreferrer">
      <span class="youtube-pick__label">Kanał Zero</span>
      <span class="youtube-pick__meta">Publicystyka, rozmowy i komentarze o polityce, społeczeństwie oraz bieżących wydarzeniach.</span>
      <strong>Otwórz kanał →</strong>
    </a>
    <a class="youtube-pick" href="https://www.youtube.com/@nawschododbliskiegowschodu/videos" target="_blank" rel="noopener noreferrer">
      <span class="youtube-pick__label">Szewko — Na Wschód od Bliskiego Wschodu</span>
      <span class="youtube-pick__meta">Najnowsze odcinki o polityce międzynarodowej, Bliskim Wschodzie, Afryce i Azji.</span>
      <strong>Zobacz najnowsze →</strong>
    </a>
  </div>
  <p class="youtube-picks__note">Polecenia redakcyjne. Brak współpracy komercyjnej.</p>
</section>"""

YOUTUBE_PICKS_BLOCK_RE = re.compile(
    r'<section\s+class=["\']youtube-picks["\'][^>]*>.*?</section>',
    re.I | re.S,
)


def write(path: str, html: str) -> None:
    (ROOT / path).write_text(html, encoding="utf-8", newline="\n")


def strip_generic_news(path: str, why_label: str) -> None:
    p = ROOT / path
    if not p.exists():
        return
    html = p.read_text(encoding="utf-8")
    before = html
    html = re.sub(
        rf'\n\s*<div class="sec"><strong>{re.escape(why_label)}:</strong>.*?</div>',
        '',
        html,
        flags=re.I | re.S,
    )
    html = re.sub(r'(<strong>Warning:</strong>)\s*(?:Warning:\s*)+', r'\1 ', html, flags=re.I)
    html = re.sub(r'(<strong>Uwaga:</strong>)\s*(?:Uwaga|Ostrożnie)\s*:\s*', r'\1 ', html, flags=re.I)
    if html != before:
        write(path, html)
        print(f"cleaned generic news comments in {path}")


def inject_hot_x_renderer(html: str) -> str:
    html = html.replace('loadHome();hot();', 'loadHome();')
    html = html.replace('<script src="/scripts/hotbar.js?v=10" defer></script>', '')
    html = HOT_X_RENDERER_RE.sub('\n', html)
    if '</body>' in html:
        return html.replace('</body>', HOT_X_SCRIPT + '\n</body>', 1)
    return html + '\n' + HOT_X_SCRIPT + '\n'


def inject_pl_youtube_picks(html: str) -> str:
    if 'id="br-youtube-picks-style"' not in html:
        if '</head>' not in html:
            raise RuntimeError('Could not add YouTube picks styles: </head> is missing')
        html = html.replace('</head>', YOUTUBE_PICKS_STYLE + '\n</head>', 1)

    if YOUTUBE_PICKS_BLOCK_RE.search(html):
        html = YOUTUBE_PICKS_BLOCK_RE.sub(YOUTUBE_PICKS_BLOCK, html, count=1)
    else:
        pattern = re.compile(r'(<div\s+class="source-feed"[^>]*></div>)', re.I)
        if not pattern.search(html):
            raise RuntimeError('Could not add YouTube picks: Hot X source-feed is missing')
        html = pattern.sub(r'\1' + YOUTUBE_PICKS_BLOCK, html, count=1)
    return html


def fix_home_en() -> None:
    path = "en/index.html"
    p = ROOT / path
    if not p.exists():
        return
    html = p.read_text(encoding="utf-8")
    before = html
    html = inject_hot_x_renderer(html)
    if html != before:
        write(path, html)
        print(f"fixed no-JS/hot-x renderer in {path}")


def fix_home_pl() -> None:
    path = "pl/index.html"
    p = ROOT / path
    if not p.exists():
        return
    html = p.read_text(encoding="utf-8")
    before = html
    html = html.replace('Otwórz na X →', 'Źródło na X →')
    html = inject_hot_x_renderer(html)
    html = inject_pl_youtube_picks(html)
    if html != before:
        write(path, html)
        print(f"fixed no-JS/hot-x/YouTube recommendations in {path}")


def main() -> None:
    strip_generic_news("en/news.html", "Why it matters")
    strip_generic_news("pl/aktualnosci.html", "Dlaczego to ważne")
    fix_home_en()
    fix_home_pl()


if __name__ == "__main__":
    main()
