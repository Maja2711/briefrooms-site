from pathlib import Path
import re

START='<!-- BR_EN_YOUTUBE_START -->'
END='<!-- BR_EN_YOUTUBE_END -->'

STYLE='''<style id="br-en-youtube-style">
.br-yt{max-width:1120px;margin:34px auto;padding:0 16px}.br-yt h2{margin:0 0 8px;font-size:clamp(24px,4vw,34px)}.br-yt>p{margin:0 0 18px;color:#9fb2c8}.br-yt-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.br-yt-card{display:flex;min-height:210px;flex-direction:column;padding:18px;border:1px solid rgba(255,255,255,.14);border-radius:20px;background:linear-gradient(180deg,rgba(255,255,255,.09),rgba(255,255,255,.035));box-shadow:0 18px 40px rgba(0,0,0,.22);color:inherit;text-decoration:none}.br-yt-card:hover{transform:translateY(-3px);border-color:rgba(56,214,201,.38)}.br-yt-tag{display:inline-flex;width:max-content;margin-bottom:14px;padding:5px 9px;border-radius:999px;background:rgba(56,214,201,.12);color:#7ff8ef;font-size:11px;font-weight:900}.br-yt-card h3{margin:0 0 10px;font-size:18px;line-height:1.25}.br-yt-card p{margin:0;color:#b8c8d8;font-size:13px;line-height:1.5}.br-yt-cta{margin-top:auto;padding-top:16px;color:#38d6c9;font-weight:900;font-size:13px}.rooms-light.rooms-books .br-yt{color:#fff1d9}.rooms-light.rooms-books .br-yt>p{color:#f3e4cf}.rooms-light.rooms-books .br-yt-card{background:rgba(55,32,20,.82);border-color:rgba(255,236,210,.25)}@media(max-width:760px){.br-yt-grid{grid-template-columns:1fr}}
</style>'''

HOME='''<section class="br-yt" aria-labelledby="br-yt-home"><h2 id="br-yt-home">Recommended on YouTube</h2><p>Three carefully selected English-language videos from AI, health and geopolitics.</p><div class="br-yt-grid">
<a class="br-yt-card" href="https://music.youtube.com/podcast/7aO3cNuUjag" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">AI</span><h3>Łukasz Kaiser on transformers, reasoning and the future of AI</h3><p>A long-form technical conversation with one of the researchers behind modern transformer systems.</p><span class="br-yt-cta">Watch on YouTube →</span></a>
<a class="br-yt-card" href="https://music.youtube.com/podcast/0rxsjNSHD_M" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">HEALTH</span><h3>High Blood Pressure: What to Eat and Avoid</h3><p>Mayo Clinic dietitians explain DASH, sodium, potassium, fibre and practical blood-pressure nutrition.</p><span class="br-yt-cta">Watch on YouTube →</span></a>
<a class="br-yt-card" href="https://music.youtube.com/podcast/sMSqb2_Ki2c" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">GEOPOLITICS</span><h3>Europe, Russia and preparedness for a wider conflict</h3><p>A discussion of European security, NATO resilience and the strategic risks posed by Russia.</p><span class="br-yt-cta">Watch on YouTube →</span></a>
</div></section>'''

HEALTH='''<section class="br-yt" aria-labelledby="br-yt-health"><h2 id="br-yt-health">Recommended on YouTube</h2><p>Evidence-based health and nutrition from recognised medical experts.</p><div class="br-yt-grid">
<a class="br-yt-card" href="https://music.youtube.com/podcast/0rxsjNSHD_M" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">MAYO CLINIC</span><h3>DASH diet guide for high blood pressure</h3><p>How sodium, potassium, fibre and food choices affect hypertension risk.</p><span class="br-yt-cta">Watch →</span></a>
<a class="br-yt-card" href="https://music.youtube.com/podcast/VKXQcNHYS1w" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">CARDIOMETABOLIC HEALTH</span><h3>Dietary Guidelines: what changed and why it matters</h3><p>Dariush Mozaffarian and Mayo Clinic discuss the evidence and practical cardiometabolic implications.</p><span class="br-yt-cta">Watch →</span></a>
</div></section>'''

SCIENCE='''<section class="br-yt" aria-labelledby="br-yt-science"><h2 id="br-yt-science">Recommended on YouTube</h2><p>AI research, model development and the science behind modern systems.</p><div class="br-yt-grid">
<a class="br-yt-card" href="https://music.youtube.com/podcast/7aO3cNuUjag" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">AI RESEARCH</span><h3>Łukasz Kaiser on transformers and reasoning models</h3><p>A technical long-form interview on the architecture and future direction of AI.</p><span class="br-yt-cta">Watch →</span></a>
<a class="br-yt-card" href="https://www.youtube.com/@OpenAI" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">OFFICIAL CHANNEL</span><h3>OpenAI research and product demonstrations</h3><p>Official talks, model demonstrations and research discussions from OpenAI.</p><span class="br-yt-cta">Open channel →</span></a>
<a class="br-yt-card" href="https://www.youtube.com/@StanfordHAI" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">STANFORD HAI</span><h3>AI governance, economics and research seminars</h3><p>Academic discussions on technical progress and the societal impact of artificial intelligence.</p><span class="br-yt-cta">Open channel →</span></a>
</div></section>'''

GEO='''<section class="br-yt" aria-labelledby="br-yt-geo"><h2 id="br-yt-geo">Recommended on YouTube</h2><p>Strategic analysis from established international-affairs institutions and specialists.</p><div class="br-yt-grid">
<a class="br-yt-card" href="https://music.youtube.com/podcast/sMSqb2_Ki2c" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">EUROPEAN SECURITY</span><h3>Europe, Russia and preparedness for conflict</h3><p>Keir Giles discusses Russian strategy, European resilience and NATO preparedness.</p><span class="br-yt-cta">Watch →</span></a>
<a class="br-yt-card" href="https://www.youtube.com/@csis" target="_blank" rel="noopener noreferrer"><span class="br-yt-tag">CSIS</span><h3>Current geopolitical briefings and strategic analysis</h3><p>China, Ukraine, the Indo-Pacific, energy security and US foreign policy.</p><span class="br-yt-cta">Open channel →</span></a>
</div></section>'''


def patch(path: str, block: str):
    p=Path(path)
    text=p.read_text(encoding='utf-8')
    text=re.sub(re.escape(START)+r'.*?'+re.escape(END), '', text, flags=re.S)
    payload=f'\n{START}\n{STYLE}\n{block}\n{END}\n'
    anchor='</main>'
    if anchor not in text:
        raise RuntimeError(f'Missing </main> in {path}')
    text=text.replace(anchor,payload+anchor,1)
    p.write_text(text,encoding='utf-8')

patch('en/health.html',HEALTH)
patch('en/science.html',SCIENCE)
patch('en/geopolitics.html',GEO)
print('Patched EN YouTube recommendations')
