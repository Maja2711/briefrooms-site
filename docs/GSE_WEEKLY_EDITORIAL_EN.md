# GSE Weekly Geopolitics Editorial — bilingual weekly publication

## Architecture and ownership
The implementation uses `scripts/gse_weekly_editorial.py`, `.github/workflows/gse-weekly-editorial.yml`, `assets/gse-weekly-editorial.css` and `tests/test_gse_weekly_editorial.py`. It consumes existing IN-03 GSE v2 public projection and writes only `data/geo_weekly/`, `pl/geo/briefing-YYYY-Www.html`, `en/geo/briefing-YYYY-Www.html`, `pl/geopolityka.html`, `en/geopolitics.html`. It never writes GSE forecasting/learning/calibration, private artifacts, Belief, trading, promotions or risk policy.

**Goal:** one well-sourced original weekly geopolitical analysis, published in both languages with a properly licensed illustrative photograph; absence of evidence never authorizes made-up content.

## Editorial pipeline
1. Sunday 09:17 Europe/Warsaw: two UTC cron candidates with a local calendar-hour gate. Manual dispatch enables governed recovery outside the Sunday slot.
2. Read only `data/gse/gse_v2_lab_public.json` (schema `gse-v2-public-lab-v3`, maximum 48h age, `shadow` and `decision_influence=false`). GSE nominates potential topics from active featured scenario types or event threat rows with `evidence_7d>=2`. **GSE probabilities, hit rates and private forecasts cannot serve as news evidence.**
3. Fetch independently dated and linked institutional UN News RSS items and independent BBC World and Guardian World RSS reporting, maximum age 8 days. Require at least 3 relevant reports, 2 publishers and 1 institutional report; otherwise **NO PUBLICATION**. Source excerpts, URLs, timestamps and organizations are frozen.
4. Generate one structured PL/EN article using the existing `scripts/sitecustomize.py` Gemini adapter and `comment_quality.request_json_completion`. Only preapproved Gemini 3.5 Flash/Flash-Lite models may run; `GEMINI_API_KEY` remains in Actions secrets. Four investor-readable angles: what changed, strategic meaning, conditional scenarios, what to watch. Each section must cite evidence IDs and contain a short literal source anchor; lead must cite 2 separate sources including an institutional source. A separate Gemini review call must pass. Model review is not a substitute for human fact-checking.
5. Select a Commons API image, JPEG/PNG/WEBP at least 800x450, only CC0, Public Domain, CC BY or CC BY-SA; require attribution/author, file link and licensing link. Image bytes are never copied into the repo. Every picture is labeled as illustrative rather than documentary evidence of current events. No eligible image => **NO PUBLICATION**.
6. Validate everything in memory before any file is written. Render PL and EN article HTML, preserve existing shelf books, insert a highlighted weekly feature and retain the latest 52 editions on bookshelf and JSON index. Each `issues/YYYY-Www.json` manifest and weekly HTML edition remains immutable.
7. GitHub Actions validation runs read-only in PR; only scheduled/manual publication has `contents:write`. GitHub concurrency, expected-week collision detection and retrying fast-forward rebase guard publication. No direct or indirect engine writeback.

## Limits and safety
- Facts must be supported by provided source titles/abstracts. News content is untrusted data, not model instructions.
- Separate confirmed facts from interpretation and scenario conditionals. Uncalibrated GSE event probabilities must not be asserted as facts.
- Avoid fake quotes, names, dates, figures, doctrines and source claims; no engine telemetry in editorial story.
- Block output if GSE topic, fresh corroboration, source reference, image license or review is inadequate. Do not silently produce substitute filler.
- Prevent repeating a topic from the two most recent editions.
- Freeze GSE public-projection SHA-256, sources, photographer/license and Gemini generator/reviewer metadata per week, without exposing private GSE evidence.
- Even with evidence anchors and independent Gemini review, semantic accuracy remains a nonzero risk requiring editorial governance.

## Validation
`python -m unittest tests.test_gse_weekly_editorial -v`; `python -m py_compile scripts/gse_weekly_editorial.py`; `python scripts/gse_weekly_editorial.py --check`. On merge, ensure an authorized `GEMINI_API_KEY` GitHub secret and current Gemini access. Old `data/ai_outlook_provider_status.json` status is not a current API preflight. Do not claim live publication until both released URLs and production workflow are verified.
