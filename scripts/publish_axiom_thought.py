#!/usr/bin/env python3
from __future__ import annotations

import json, os, re, subprocess, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests

ROOT=Path(__file__).resolve().parents[1]
CURRENT=ROOT/"data/home/axiom-thought.json"
HISTORY=ROOT/"data/home/axiom-thoughts-history.jsonl"
RESERVE=ROOT/"data/home/axiom-thought-reserve.json"
PRINCIPLES=ROOT/"docs/axiom-thought-principles.md"
GUARD=ROOT/"scripts/axiom_thought_guard.py"
WARSAW=ZoneInfo("Europe/Warsaw")
MODEL=os.getenv("AXIOM_THOUGHT_MODEL","gemini-3.5-flash")
FALLBACK_MODEL=os.getenv("AXIOM_THOUGHT_FALLBACK_MODEL","gemini-3.5-flash-lite")
MAX_ATTEMPTS=int(os.getenv("AXIOM_THOUGHT_MAX_ATTEMPTS","10"))
RESERVE_TARGET=int(os.getenv("AXIOM_THOUGHT_RESERVE_TARGET","10"))

def today(): return datetime.now(WARSAW).date().isoformat()
def history_rows(): return [json.loads(x) for x in HISTORY.read_text(encoding="utf-8").splitlines() if x.strip()]
def reserve_rows():
    if not RESERVE.exists(): return []
    data=json.loads(RESERVE.read_text(encoding="utf-8"))
    return data if isinstance(data,list) else []

def save_reserve(rows): RESERVE.write_text(json.dumps(rows,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def extract_json(text):
    text=re.sub(r"^\x60\x60\x60(?:json)?\s*","",text.strip())
    text=re.sub(r"\s*\x60\x60\x60$","",text)
    a,b=text.find("{"),text.rfind("}")
    if a<0 or b<a: raise ValueError("model returned no JSON object")
    return json.loads(text[a:b+1])

def call_gemini(prompt,model):
    key=os.environ.get("GEMINI_API_KEY","").strip()
    if not key: raise RuntimeError("GEMINI_API_KEY is missing")
    url=f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
    payload={"contents":[{"parts":[{"text":prompt}]}],"generationConfig":{"temperature":1.2,"responseMimeType":"application/json"}}
    r=requests.post(url,json=payload,timeout=90); r.raise_for_status()
    return extract_json(r.json()["candidates"][0]["content"]["parts"][0]["text"])

def build_prompt(rows,reserve,mode):
    archive="\n".join(json.dumps(r,ensure_ascii=False) for r in rows)
    reserved="\n".join(json.dumps(r,ensure_ascii=False) for r in reserve)
    return f"""You are AXIOM, co-author of BriefRooms. Create a Morning AXIOM thought.
This is NOT motivational quote generation. Intellectual depth and originality are mandatory.
MODE: {mode}. DATE: {today()}
EDITORIAL CONSTITUTION:
{PRINCIPLES.read_text(encoding="utf-8")}
PUBLISHED ARCHIVE:
{archive}
ALREADY VALIDATED RESERVE (do not repeat these ideas):
{reserved}
Internally generate and compare AT LEAST 10 conceptually different candidates from different domains.
Reject slogans, corporate wisdom, self-help, obvious truths, famous-quote paraphrases, and anything semantically close to archive or reserve.
Return ONLY one JSON object with exactly:
{{"theme":"short-kebab-case-theme","pl":"„Polish thought”","en":"“faithful English translation”","candidates_considered":10,"scores":{{"depth":8,"novelty":8,"banality_risk":0}},"silence_test":true,"editor_note":"minimum 80 characters explaining the non-obvious insight and originality"}}
Use honest scores. Rethink weak candidates before returning."""

def validate_candidate(c,rows,extra=None):
    required={"theme","pl","en","candidates_considered","scores","silence_test","editor_note"}
    if set(c)!=required: raise ValueError("candidate keys mismatch")
    stamp=today()
    record={"date":stamp,**c}
    current={"date":stamp,"author":"AXIOM","brand":"BriefRooms","pl":c["pl"],"en":c["en"]}
    sys.path.insert(0,str(ROOT/"scripts"))
    from axiom_thought_guard import validate, similarity
    errors=validate(current,rows+[record])
    if errors: raise ValueError("; ".join(errors))
    for other in extra or []:
        sim=similarity(c["pl"],other.get("pl",""))
        if sim.violates or c["theme"]==other.get("theme"):
            raise ValueError("candidate too similar to reserve")

def generate_one(rows,reserve,mode):
    prompt=build_prompt(rows,reserve,mode)
    errors=[]
    for attempt in range(1,MAX_ATTEMPTS+1):
        model=MODEL if attempt <= max(1,MAX_ATTEMPTS-2) else FALLBACK_MODEL
        try:
            c=call_gemini(prompt+f"\nAttempt {attempt}/{MAX_ATTEMPTS}: choose a genuinely different, deeper idea.",model)
            validate_candidate(c,rows,reserve)
            return c
        except Exception as exc:
            errors.append(f"{attempt}/{model}: {exc}"); time.sleep(2)
    raise RuntimeError("no candidate passed after "+str(MAX_ATTEMPTS)+" attempts: "+" | ".join(errors[-3:]))

def publish(candidate,rows,source):
    stamp=today()
    record={"date":stamp,**candidate,"source":source}
    # Guard schema intentionally excludes source; validate before adding audit metadata.
    validate_candidate(candidate,rows)
    history_record={"date":stamp,**candidate}
    HISTORY.write_text(HISTORY.read_text(encoding="utf-8").rstrip()+"\n"+json.dumps(history_record,ensure_ascii=False,separators=(",",":"))+"\n",encoding="utf-8")
    CURRENT.write_text(json.dumps({"date":stamp,"author":"AXIOM","brand":"BriefRooms","pl":candidate["pl"],"en":candidate["en"]},ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    if subprocess.run([sys.executable,str(GUARD)],cwd=ROOT).returncode: raise RuntimeError("post-write guard failed")
    print(f"AXIOM Thought: published {stamp} source={source} theme={candidate['theme']}")

def main():
    rows=history_rows(); reserve=reserve_rows(); stamp=today()
    if rows and rows[-1].get("date")==stamp:
        print(f"AXIOM Thought: already published for {stamp}")
    else:
        try:
            publish(generate_one(rows,reserve,"daily"),rows,"generated")
        except Exception as exc:
            print(f"AXIOM Thought: live generation failed: {exc}",file=sys.stderr)
            published=False
            while reserve:
                candidate=reserve.pop(0); save_reserve(reserve)
                try:
                    validate_candidate(candidate,rows,reserve)
                    publish(candidate,rows,"reserve")
                    published=True; break
                except Exception as reserve_exc:
                    print(f"AXIOM Thought: rejected stale reserve item: {reserve_exc}",file=sys.stderr)
            if not published: raise RuntimeError("live generation failed and validated reserve is empty")
    # Refill reserve after publication. Failure here must not undo today's successful publication.
    rows=history_rows(); reserve=reserve_rows()
    refill_errors=0
    while len(reserve)<RESERVE_TARGET:
        try:
            c=generate_one(rows,reserve,"reserve-refill")
            reserve.append(c); save_reserve(reserve)
            print(f"AXIOM Thought: reserve {len(reserve)}/{RESERVE_TARGET}")
        except Exception as exc:
            refill_errors+=1
            print(f"AXIOM Thought: reserve refill stopped: {exc}",file=sys.stderr)
            break
    return 0

if __name__=="__main__": raise SystemExit(main())
