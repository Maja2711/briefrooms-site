#!/usr/bin/env python3
import json
from pathlib import Path
P=Path("data/investments/deepbook_predict_shadow.json")
def brier(p,y): return (float(p)-float(y))**2
def score(state):
    settlements=state.get("settlements",{})
    out=[]
    for batch in state.get("snapshots",[]):
        for q in batch.get("quotes",[]):
            if q.get("up_probability") is None: continue
            px=settlements.get(str(q.get("expiry_ms")))
            if px is None: continue
            y=int(float(px)>float(q["strike"]))
            out.append({"expiry_ms":q["expiry_ms"],"strike":q["strike"],"p":q["up_probability"],"outcome":y,"brier":brier(q["up_probability"],y)})
    return out
if __name__=="__main__":
    s=json.loads(P.read_text()) if P.exists() else {}
    rows=score(s); s["scores"]=rows
    s["deepbook_brier_mean"]=sum(x["brier"] for x in rows)/len(rows) if rows else None
    P.write_text(json.dumps(s,indent=2)+"\n")
    print(json.dumps({"scored":len(rows),"brier":s["deepbook_brier_mean"]}))
