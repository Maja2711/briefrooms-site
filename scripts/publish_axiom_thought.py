#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
CURRENT=ROOT/"data/home/axiom-thought.json"; HISTORY=ROOT/"data/home/axiom-thoughts-history.jsonl"
RESERVE=ROOT/"data/home/axiom-thought-reserve.json"; PRINCIPLES=ROOT/"docs/axiom-thought-principles.md"
GUARD=ROOT/"scripts/axiom_thought_guard.py"; WARSAW=ZoneInfo("Europe/Warsaw")
MODEL=os.getenv("AXIOM_THOUGHT_MODEL","gemini-3.6-flash")
FALLBACK=os.getenv("AXIOM_THOUGHT_FALLBACK_MODEL","gemini-3.5-flash-lite")
MAX_ATTEMPTS=int(os.getenv("AXIOM_THOUGHT_MAX_ATTEMPTS","12"))
RESERVE_TARGET=int(os.getenv("AXIOM_THOUGHT_RESERVE_TARGET","10"))
HISTORY_MEMORY_DAYS=365

def today(): return datetime.now(WARSAW).date().isoformat()
def load_history(): return [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
def load_reserve():
    if not RESERVE.exists(): return []
    x=json.loads(RESERVE.read_text(encoding="utf-8")); return x if isinstance(x,list) else []
def save_reserve(x): RESERVE.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def extract_json(s):
    s=re.sub(r"^\x60\x60\x60(?:json)?\s*","",s.strip()); s=re.sub(r"\s*\x60\x60\x60$","",s)
    a,b=s.find("{"),s.rfind("}")
    if a<0 or b<a: raise ValueError("no JSON object")
    return json.loads(s[a:b+1])
def call(prompt,model):
    import requests
    key=os.getenv("GEMINI_API_KEY","").strip()
    if not key: raise RuntimeError("GEMINI_API_KEY missing")
    r=requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
      json={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"responseMimeType":"application/json"}},timeout=90)
    r.raise_for_status(); return extract_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])
def protected_history(rows):
    cutoff=datetime.now(WARSAW).date()-timedelta(days=HISTORY_MEMORY_DAYS)
    return [x for x in rows if x.get("date") and datetime.fromisoformat(x["date"]).date() >= cutoff]

def prompt(rows,reserve,mode):
    memory=protected_history(rows)
    return f"""You are AXIOM, co-author of BriefRooms. Produce one original Morning AXIOM thought.
MODE={mode}; DATE={today()}
CONSTITUTION:
{PRINCIPLES.read_text(encoding="utf-8")}
PROTECTED PUBLISHED HISTORY — LAST 365 DAYS. ANY semantic repeat, paraphrase, reused thesis or reused theme is forbidden:
{chr(10).join(json.dumps(x,ensure_ascii=False) for x in memory)}
RESERVE - DO NOT REPEAT:
{chr(10).join(json.dumps(x,ensure_ascii=False) for x in reserve)}
Internally compare at least 10 genuinely different ideas. Before returning, compare the candidate against EVERY protected history item. Reject slogans, self-help, obvious truths, quote paraphrases, reused theses, reused themes and semantic repeats. A candidate must add a genuinely new conceptual claim, not merely new wording.
Return ONLY JSON with exactly:
{{"theme":"kebab-case","pl":"„Polish thought”","en":"“English translation”","candidates_considered":10,"scores":{{"depth":9,"novelty":9,"banality_risk":1}},"silence_test":true,"editor_note":"at least 80 characters explaining the non-obvious insight and originality"}}"""
def candidate_validation_stamp(rows, base_stamp=None):
    stamp=base_stamp or today()
    published_dates={x.get("date") for x in rows}
    while stamp in published_dates:
        stamp=(datetime.fromisoformat(stamp)+timedelta(days=1)).date().isoformat()
    return stamp

def validate_candidate(c,rows,reserve=()):
    required={"theme","pl","en","candidates_considered","scores","silence_test","editor_note"}
    if set(c)!=required: raise ValueError("schema mismatch")
    # Reserve generation may run after today's thought has already been published.
    # Validate a reserve candidate against a synthetic next publication date so
    # the guard does not see today's already-published date twice.
    stamp=candidate_validation_stamp(rows)
    cur={"date":stamp,"author":"AXIOM","brand":"BriefRooms","pl":c["pl"],"en":c["en"]}
    sys.path.insert(0,str(ROOT/"scripts")); from axiom_thought_guard import validate, similarity
    errors=validate(cur,rows+[{"date":stamp,**c}])
    if errors: raise ValueError("; ".join(errors))
    for x in reserve:
        if c["theme"]==x.get("theme") or similarity(c["pl"],x.get("pl","")).violates: raise ValueError("too similar to reserve")
def generate(rows,reserve,mode):
    errs=[]
    for n in range(1,MAX_ATTEMPTS+1):
        model=MODEL if n<=MAX_ATTEMPTS-3 else FALLBACK
        try:
            c=call(prompt(rows,reserve,mode)+f"\nAttempt {n}/{MAX_ATTEMPTS}; force a different conceptual domain.",model)
            validate_candidate(c,rows,reserve); return c
        except Exception as e: errs.append(str(e)); time.sleep(2)
    raise RuntimeError("all generation attempts failed; last="+(errs[-1] if errs else "unknown"))
def publish(c,rows,source):
    validate_candidate(c,rows)
    stamp=today(); rec={"date":stamp,**c}
    old=HISTORY.read_text(encoding="utf-8").rstrip()
    HISTORY.write_text(old+"\n"+json.dumps(rec,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    CURRENT.write_text(json.dumps({"date":stamp,"author":"AXIOM","brand":"BriefRooms","pl":c["pl"],"en":c["en"]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if subprocess.run([sys.executable,str(GUARD)],cwd=ROOT).returncode: raise RuntimeError("post-publication guard failed")
    print(f"PUBLISHED {stamp} source={source} theme={c['theme']}")
def refill(rows,reserve):
    failures=0
    while len(reserve)<RESERVE_TARGET and failures<3:
        try:
            c=generate(rows,reserve,"reserve"); reserve.append(c); save_reserve(reserve); failures=0
            print(f"RESERVE {len(reserve)}/{RESERVE_TARGET}")
        except Exception as e:
            failures+=1; print(f"reserve generation failure {failures}/3: {e}",file=sys.stderr)
    return len(reserve)>=RESERVE_TARGET
def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--mode",choices=["publish","refill","verify","preflight"],default="publish"); a=ap.parse_args()
    rows=load_history(); reserve=load_reserve(); stamp=today()
    if a.mode=="verify":
        cur=json.loads(CURRENT.read_text(encoding="utf-8"))
        ok=cur.get("date")==stamp and rows and rows[-1].get("date")==stamp
        print(f"fresh={ok} current={cur.get('date')} expected={stamp} reserve={len(reserve)}"); return 0 if ok else 1
    if a.mode=="preflight":
        if not reserve:
            print("preflight failed: reserve empty", file=sys.stderr); return 2
        errors=[]
        for i,candidate in enumerate(reserve):
            try:
                validate_candidate(candidate,rows,reserve[:i])
            except Exception as e:
                errors.append(f"reserve[{i}] {candidate.get('theme')}: {e}")
        if errors:
            print("preflight failed:\n" + "\n".join(errors), file=sys.stderr); return 2
        print(f"preflight=ok next_date={candidate_validation_stamp(rows)} reserve={len(reserve)} first_theme={reserve[0].get('theme')}")
        return 0
    if a.mode=="refill": return 0 if refill(rows,reserve) else 2
    if rows and rows[-1].get("date")==stamp:
        print("already published"); return 0
    # Fail-safe order is deliberate: prevalidated reserve first, live AI second.
    while reserve:
        c=reserve.pop(0); save_reserve(reserve)
        try: publish(c,rows,"reserve"); return 0
        except Exception as e: print(f"reserve candidate rejected: {e}",file=sys.stderr)
    try: c=generate(rows,[],"live"); publish(c,rows,"live"); return 0
    except Exception as e: print(f"publication failed: {e}",file=sys.stderr); return 2
if __name__=="__main__": raise SystemExit(main())
