#!/usr/bin/env python3
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

EN_STYLE = '''<style id="br-en-youtube-style">
.br-yt{max-width:1120px;margin:34px auto;padding:0 16px}.br-yt h2{margin:0 0 8px;font-size:clamp(24px,4vw,34px)}.br-yt>p{margin:0 0 18px;color:#9fb2c8}.br-yt-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.br-yt-card{display:flex;min-height:210px;flex-direction:column;padding:18px;border:1px solid rgba(255,255,255,.14);border-radius:20px;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.035));box-shadow:0 18px 40px rgba(0,0,0,.22);color:inherit;text-decoration:none;transition:transform .18s ease,border-color .18s ease}.br-yt-card:hover{transform:translateY(-3px);border-color:rgba(56,214,201,.38)}.br-yt-card__identity{display:flex;align-items:center;gap:12px;margin-bottom:12px;min-width:0}.br-yt-card__copy{min-width:0}.br-yt-thumb{width:72px;height:52px;flex:0 0 72px;border:1px solid rgba(209,240,255,.2);border-radius:12px;background:#07131e;object-fit:cover;box-shadow:0 8px 20px rgba(0,0,0,.28)}.br-yt-thumb--avatar{width:56px;height:56px;flex-basis:56px;border-radius:50%;background:#fff;object-fit:cover}.br-yt-tag{display:inline-flex;width:max-content;margin:0 0 6px;padding:5px 9px;border-radius:999px;background:rgba(56,214,201,.12);color:#7ff8ef;font-size:11px;font-weight:900}.br-yt-card h3{margin:0;font-size:18px;line-height:1.25}.br-yt-card p{margin:0;color:#b8c8d8;font-size:13px;line-height:1.5}.br-yt-cta{margin-top:auto;padding-top:16px;color:#38d6c9;font-weight:900;font-size:13px}.rooms-light.rooms-books .br-yt{color:#fff1d9}.rooms-light.rooms-books .br-yt>p{color:#f3e4cf}.rooms-light.rooms-books .br-yt-card{background:rgba(55,32,20,.82);border-color:rgba(255,236,210,.25)}@media(max-width:760px){.br-yt-grid{grid-template-columns:1fr}}@media(max-width:460px){.br-yt-thumb{width:64px;height:48px;flex-basis:64px}.br-yt-thumb--avatar{width:50px;height:50px;flex-basis:50px}}
</style>'''


def en_video_card(href, image, tag, title, desc, cta):
    return f'''<a class="br-yt-card" href="{href}" target="_blank" rel="noopener noreferrer"><div class="br-yt-card__identity"><img class="br-yt-thumb" src="{image}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><div class="br-yt-card__copy"><span class="br-yt-tag">{tag}</span><h3>{title}</h3></div></div><p>{desc}</p><span class="br-yt-cta">{cta}</span></a>'''


def en_channel_card(href, avatar, tag, title, desc, cta):
    return f'''<a class="br-yt-card" href="{href}" target="_blank" rel="noopener noreferrer"><div class="br-yt-card__identity"><img class="br-yt-thumb br-yt-thumb--avatar" src="{avatar}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><div class="br-yt-card__copy"><span class="br-yt-tag">{tag}</span><h3>{title}</h3></div></div><p>{desc}</p><span class="br-yt-cta">{cta}</span></a>'''


HOME = '''<section class="br-yt" aria-labelledby="br-yt-home"><h2 id="br-yt-home">Recommended on YouTube</h2><p>Three carefully selected English-language videos from AI, health and geopolitics.</p><div class="br-yt-grid">\n''' + '\n'.join([
    en_video_card('https://music.youtube.com/podcast/7aO3cNuUjag', 'https://i.ytimg.com/vi/7aO3cNuUjag/hqdefault.jpg', 'AI', 'Łukasz Kaiser on transformers, reasoning and the future of AI', 'A long-form technical conversation with one of the researchers behind modern transformer systems.', 'Watch on YouTube →'),
    en_video_card('https://music.youtube.com/podcast/0rxsjNSHD_M', 'https://i.ytimg.com/vi/0rxsjNSHD_M/hqdefault.jpg', 'HEALTH', 'High Blood Pressure: What to Eat and Avoid', 'Mayo Clinic dietitians explain DASH, sodium, potassium, fibre and practical blood-pressure nutrition.', 'Watch on YouTube →'),
    en_video_card('https://music.youtube.com/podcast/sMSqb2_Ki2c', 'https://i.ytimg.com/vi/sMSqb2_Ki2c/hqdefault.jpg', 'GEOPOLITICS', 'Europe, Russia and preparedness for a wider conflict', 'A discussion of European security, NATO resilience and the strategic risks posed by Russia.', 'Watch on YouTube →'),
]) + '\n</div></section>'

HEALTH = '''<section class="br-yt" aria-labelledby="br-yt-health"><h2 id="br-yt-health">Recommended on YouTube</h2><p>Evidence-based health and nutrition from recognised medical experts.</p><div class="br-yt-grid">\n''' + '\n'.join([
    en_video_card('https://music.youtube.com/podcast/0rxsjNSHD_M', 'https://i.ytimg.com/vi/0rxsjNSHD_M/hqdefault.jpg', 'MAYO CLINIC', 'DASH diet guide for high blood pressure', 'How sodium, potassium, fibre and food choices affect hypertension risk.', 'Watch →'),
    en_video_card('https://music.youtube.com/podcast/VKXQcNHYS1w', 'https://i.ytimg.com/vi/VKXQcNHYS1w/hqdefault.jpg', 'CARDIOMETABOLIC HEALTH', 'Dietary Guidelines: what changed and why it matters', 'Dariush Mozaffarian and Mayo Clinic discuss the evidence and practical cardiometabolic implications.', 'Watch →'),
]) + '\n</div></section>'

SCIENCE = '''<section class="br-yt" aria-labelledby="br-yt-science"><h2 id="br-yt-science">Recommended on YouTube</h2><p>AI research, model development and the science behind modern systems.</p><div class="br-yt-grid">\n''' + '\n'.join([
    en_video_card('https://music.youtube.com/podcast/7aO3cNuUjag', 'https://i.ytimg.com/vi/7aO3cNuUjag/hqdefault.jpg', 'AI RESEARCH', 'Łukasz Kaiser on transformers and reasoning models', 'A technical long-form interview on the architecture and future direction of AI.', 'Watch →'),
    en_channel_card('https://www.youtube.com/@OpenAI', 'https://unavatar.io/youtube/OpenAI', 'OFFICIAL CHANNEL', 'OpenAI research and product demonstrations', 'Official talks, model demonstrations and research discussions from OpenAI.', 'Open channel →'),
    en_channel_card('https://www.youtube.com/@StanfordHAI', 'https://unavatar.io/youtube/StanfordHAI', 'STANFORD HAI', 'AI governance, economics and research seminars', 'Academic discussions on technical progress and the societal impact of artificial intelligence.', 'Open channel →'),
]) + '\n</div></section>'

GEO = '''<section class="br-yt" aria-labelledby="br-yt-geo"><h2 id="br-yt-geo">Recommended on YouTube</h2><p>Strategic analysis from established international-affairs institutions and specialists.</p><div class="br-yt-grid">\n''' + '\n'.join([
    en_video_card('https://music.youtube.com/podcast/sMSqb2_Ki2c', 'https://i.ytimg.com/vi/sMSqb2_Ki2c/hqdefault.jpg', 'EUROPEAN SECURITY', 'Europe, Russia and preparedness for conflict', 'Keir Giles discusses Russian strategy, European resilience and NATO preparedness.', 'Watch →'),
    en_channel_card('https://www.youtube.com/@csis', 'https://unavatar.io/youtube/csis', 'CSIS', 'Current geopolitical briefings and strategic analysis', 'China, Ukraine, the Indo-Pacific, energy security and US foreign policy.', 'Open channel →'),
]) + '\n</div></section>'

PL_HOME_STYLE = '''<style id="br-youtube-picks-style">
.youtube-picks{margin-top:24px;padding-top:22px;border-top:1px solid rgba(255,255,255,.12)}
.youtube-picks__head{display:flex;align-items:flex-start;gap:11px;margin-bottom:12px}
.youtube-picks__icon{display:grid;place-items:center;width:32px;height:32px;flex:0 0 32px;border:1px solid rgba(255,102,102,.34);border-radius:10px;background:linear-gradient(145deg,rgba(255,90,90,.24),rgba(120,20,30,.18));color:#ff8c91;font-size:13px;box-shadow:inset 0 1px 0 rgba(255,255,255,.16)}
.youtube-picks__head h3{margin:0 0 4px;color:#eef7ff;font-size:18px;line-height:1.15}
.youtube-picks__head p{margin:0;color:#8fa6ba;font-size:12px;line-height:1.4}
.youtube-picks__grid{display:grid;gap:10px}
.youtube-pick{display:flex;min-width:0;flex-direction:column;gap:5px;padding:14px 15px;border:1px solid rgba(149,216,255,.14);border-radius:16px;background:linear-gradient(145deg,rgba(36,79,108,.34),rgba(11,34,52,.42));box-shadow:inset 0 1px 0 rgba(231,249,255,.08),0 12px 28px rgba(0,0,0,.16);text-decoration:none;transition:transform .18s ease,border-color .18s ease,background .18s ease}
.youtube-pick:hover{transform:translateY(-2px);border-color:rgba(111,215,224,.34);background:linear-gradient(145deg,rgba(42,91,123,.42),rgba(13,40,61,.48));text-decoration:none}
.youtube-pick__identity{display:flex;align-items:center;gap:11px;margin-bottom:2px;min-width:0}
.youtube-pick__avatar{width:48px;height:48px;flex:0 0 48px;border:1.5px solid rgba(214,245,255,.45);border-radius:50%;background:#fff;object-fit:cover;box-shadow:0 7px 18px rgba(0,0,0,.28)}
.youtube-pick__label{color:#edf8ff!important;font-size:14px!important;font-weight:900!important;line-height:1.3!important}
.youtube-pick__meta{color:#9eb3c5!important;font-size:11px!important;font-weight:600!important;line-height:1.45!important}
.youtube-pick strong{margin-top:3px;color:#71dfe5;font-size:12px}
.youtube-picks__note{margin:11px 2px 0!important;color:#6f879b!important;font-size:10px!important;line-height:1.4!important}
@media(max-width:460px){.youtube-pick__avatar{width:44px;height:44px;flex-basis:44px}}
@media(prefers-reduced-motion:reduce){.youtube-pick{transition:none}}
</style>'''

PL_HOME_BLOCK = '''<section class="youtube-picks" aria-labelledby="youtube-picks-title">
  <div class="youtube-picks__head">
    <span class="youtube-picks__icon" aria-hidden="true">▶</span>
    <div><h3 id="youtube-picks-title">Polecane na YouTube</h3><p>Wybrane kanały publicystyczne i analityczne.</p></div>
  </div>
  <div class="youtube-picks__grid">
    <a class="youtube-pick" href="https://www.youtube.com/@kotwarty" target="_blank" rel="noopener noreferrer">
      <span class="youtube-pick__identity"><img class="youtube-pick__avatar" src="https://unavatar.io/youtube/kotwarty" alt="Avatar kanału Kanał Otwarty" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><span class="youtube-pick__label">Kanał Otwarty</span></span>
      <span class="youtube-pick__meta">Rozmowy, publicystyka i analiza bieżących wydarzeń.</span>
      <strong>Otwórz kanał →</strong>
    </a>
    <a class="youtube-pick" href="https://www.youtube.com/@KanalZeroPL" target="_blank" rel="noopener noreferrer">
      <span class="youtube-pick__identity"><img class="youtube-pick__avatar" src="https://unavatar.io/youtube/KanalZeroPL" alt="Avatar kanału Kanał Zero" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><span class="youtube-pick__label">Kanał Zero</span></span>
      <span class="youtube-pick__meta">Publicystyka, rozmowy i komentarze o polityce, społeczeństwie oraz bieżących wydarzeniach.</span>
      <strong>Otwórz kanał →</strong>
    </a>
    <a class="youtube-pick" href="https://www.youtube.com/@nawschododbliskiegowschodu/videos" target="_blank" rel="noopener noreferrer">
      <span class="youtube-pick__identity"><img class="youtube-pick__avatar" src="https://unavatar.io/youtube/nawschododbliskiegowschodu" alt="Avatar kanału Na Wschód od Bliskiego Wschodu" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><span class="youtube-pick__label">Szewko — Na Wschód od Bliskiego Wschodu</span></span>
      <span class="youtube-pick__meta">Najnowsze odcinki o polityce międzynarodowej, Bliskim Wschodzie, Afryce i Azji.</span>
      <strong>Zobacz najnowsze →</strong>
    </a>
  </div>
  <p class="youtube-picks__note">Polecenia redakcyjne. Brak współpracy komercyjnej.</p>
</section>'''


def replace_triple_assignment(text, name, value):
    pattern = re.compile(rf"{re.escape(name)}\s*=\s*'''(?:.|\n)*?'''", re.S)
    if not pattern.search(text):
        raise RuntimeError(f'Missing assignment {name}')
    return pattern.sub(lambda _: f"{name}='''{value}'''", text, count=1)


def patch_en_generator():
    path = ROOT / 'scripts/patch_en_youtube_recommendations.py'
    text = path.read_text(encoding='utf-8')
    for name, value in [('STYLE', EN_STYLE), ('HOME', HOME), ('HEALTH', HEALTH), ('SCIENCE', SCIENCE), ('GEO', GEO)]:
        text = replace_triple_assignment(text, name, value)
    if "patch('en/index.html',HOME)" not in text:
        text = text.replace("patch('en/health.html',HEALTH)", "patch('en/index.html',HOME)\npatch('en/health.html',HEALTH)", 1)
    path.write_text(text, encoding='utf-8', newline='\n')


def patch_en_workflow():
    path = ROOT / '.github/workflows/publish-en-youtube-recommendations.yml'
    text = path.read_text(encoding='utf-8')
    text = text.replace("files=['en/health.html','en/science.html','en/geopolitics.html']", "files=['en/index.html','en/health.html','en/science.html','en/geopolitics.html']")
    text = text.replace('git add en/health.html en/science.html en/geopolitics.html', 'git add en/index.html en/health.html en/science.html en/geopolitics.html')
    path.write_text(text, encoding='utf-8', newline='\n')


def patch_pl_home_generator():
    path = ROOT / 'scripts/fix_static_quality.py'
    text = path.read_text(encoding='utf-8')
    text = replace_triple_assignment(text, 'YOUTUBE_PICKS_STYLE', PL_HOME_STYLE)
    text = replace_triple_assignment(text, 'YOUTUBE_PICKS_BLOCK', PL_HOME_BLOCK)
    path.write_text(text, encoding='utf-8', newline='\n')


def patch_pl_geopolitics():
    path = ROOT / 'pl/geopolityka.html'
    text = path.read_text(encoding='utf-8')
    if '.youtube-pick__identity{' not in text:
        marker = '    .youtube-pick strong{\n'
        addition = '''    .youtube-pick__identity{\n      display:flex;\n      align-items:center;\n      gap:13px;\n      margin-bottom:11px;\n      min-width:0;\n    }\n\n    .youtube-pick__avatar{\n      width:62px;\n      height:62px;\n      flex:0 0 62px;\n      border:2px solid rgba(255,239,218,.58);\n      border-radius:50%;\n      background:#fff;\n      object-fit:cover;\n      box-shadow:0 8px 20px rgba(0,0,0,.3);\n    }\n\n'''
        if marker not in text:
            raise RuntimeError('Missing geopolitics strong style marker')
        text = text.replace(marker, addition + marker, 1)
    text = re.sub(r'(\.youtube-pick strong\{\s*)margin-bottom:9px;', r'\1margin:0;', text, count=1)
    text = text.replace('.youtube-pick span:not(.youtube-pick__label){', '.youtube-pick__description{', 1)
    text = text.replace('      .youtube-picks__head{align-items:flex-start}\n', '      .youtube-picks__head{align-items:flex-start}\n      .youtube-pick__avatar{width:54px;height:54px;flex-basis:54px}\n', 1)

    replacements = [
        (
'''          <span class="youtube-pick__label">Strategia i bezpieczeństwo</span>\n          <strong>Strategy&amp;Future</strong>\n          <span>Rozmowy i analizy dotyczące rywalizacji mocarstw, bezpieczeństwa Polski, wojskowości i przyszłego ładu międzynarodowego.</span>''',
'''          <span class="youtube-pick__label">Strategia i bezpieczeństwo</span>\n          <div class="youtube-pick__identity"><img class="youtube-pick__avatar" src="https://unavatar.io/youtube/StrategyFuture" alt="Avatar kanału Strategy&amp;Future" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><strong>Strategy&amp;Future</strong></div>\n          <span class="youtube-pick__description">Rozmowy i analizy dotyczące rywalizacji mocarstw, bezpieczeństwa Polski, wojskowości i przyszłego ładu międzynarodowego.</span>'''),
        (
'''          <span class="youtube-pick__label">Bliski Wschód i świat</span>\n          <strong>Szewko — Na Wschód od Bliskiego Wschodu</strong>\n          <span>Szeroki przegląd wydarzeń z Bliskiego Wschodu, Afryki i Azji, uzupełniony kontekstem historycznym, politycznym i kulturowym.</span>''',
'''          <span class="youtube-pick__label">Bliski Wschód i świat</span>\n          <div class="youtube-pick__identity"><img class="youtube-pick__avatar" src="https://unavatar.io/youtube/nawschododbliskiegowschodu" alt="Avatar kanału Na Wschód od Bliskiego Wschodu" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><strong>Szewko — Na Wschód od Bliskiego Wschodu</strong></div>\n          <span class="youtube-pick__description">Szeroki przegląd wydarzeń z Bliskiego Wschodu, Afryki i Azji, uzupełniony kontekstem historycznym, politycznym i kulturowym.</span>'''),
        (
'''          <span class="youtube-pick__label">Europa Wschodnia</span>\n          <strong>OSW — Ośrodek Studiów Wschodnich</strong>\n          <span>Rzeczowe materiały o Rosji, Ukrainie, Europie Środkowej, Kaukazie i Azji Centralnej przygotowywane przez analityków OSW.</span>''',
'''          <span class="youtube-pick__label">Europa Wschodnia</span>\n          <div class="youtube-pick__identity"><img class="youtube-pick__avatar" src="https://unavatar.io/youtube/OSWOsrodekstudiowwschodnich" alt="Avatar kanału OSW" loading="lazy" decoding="async" referrerpolicy="no-referrer" /><strong>OSW — Ośrodek Studiów Wschodnich</strong></div>\n          <span class="youtube-pick__description">Rzeczowe materiały o Rosji, Ukrainie, Europie Środkowej, Kaukazie i Azji Centralnej przygotowywane przez analityków OSW.</span>'''),
    ]
    for old, new in replacements:
        if old in text:
            text = text.replace(old, new, 1)
        elif new not in text:
            raise RuntimeError('Missing expected geopolitics YouTube card markup')
    path.write_text(text, encoding='utf-8', newline='\n')


def main():
    patch_en_generator()
    patch_en_workflow()
    patch_pl_home_generator()
    subprocess.run([sys.executable, str(ROOT / 'scripts/patch_en_youtube_recommendations.py')], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/fix_static_quality.py')], cwd=ROOT, check=True)
    patch_pl_geopolitics()

    checks = {
        'pl/index.html': ['youtube-pick__avatar', 'unavatar.io/youtube/kotwarty'],
        'pl/geopolityka.html': ['youtube-pick__avatar', 'unavatar.io/youtube/StrategyFuture'],
        'pl/zdrowie.html': ['health-youtube-pick__avatar'],
        'en/index.html': ['br-yt-thumb', 'i.ytimg.com/vi/7aO3cNuUjag'],
        'en/health.html': ['br-yt-thumb', 'i.ytimg.com/vi/0rxsjNSHD_M'],
        'en/science.html': ['br-yt-thumb', 'unavatar.io/youtube/OpenAI'],
        'en/geopolitics.html': ['br-yt-thumb', 'unavatar.io/youtube/csis'],
    }
    for rel, needles in checks.items():
        body = (ROOT / rel).read_text(encoding='utf-8')
        for needle in needles:
            if needle not in body:
                raise RuntimeError(f'{needle!r} missing from {rel}')
    print('YouTube thumbnail UI applied and validated.')


if __name__ == '__main__':
    main()
