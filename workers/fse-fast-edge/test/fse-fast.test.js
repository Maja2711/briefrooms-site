import test from "node:test";
import assert from "node:assert/strict";
import {resampleMinutes,phaseDescriptor,crossScaleAlignment} from "../src/index.js";

function syntheticBars(n=180) {
  const start=Date.parse("2026-10-07T10:00:00Z");
  let p=100;
  const rows=[];
  for (let i=0;i<n;i+=1) {
    const old=p;
    p*=Math.exp(0.0002+Math.sin(i/11)*0.0004);
    rows.push({
      timestamp:new Date(start+i*60_000).toISOString(),
      open:old,high:Math.max(old,p),low:Math.min(old,p),close:p,volume:1,
    });
  }
  return rows;
}

test("resampleMinutes produces aligned five-minute bars",()=>{
  const out=resampleMinutes(syntheticBars(20),5);
  assert.equal(out.length,4);
  assert.equal(out[0].timestamp,"2026-10-07T10:00:00.000Z");
  assert.equal(out[1].timestamp,"2026-10-07T10:05:00.000Z");
});

test("phaseDescriptor exposes the canonical direction contract",async()=>{
  const out=await phaseDescriptor(syntheticBars());
  assert.equal(out.available,true);
  assert.ok(["UP","DOWN","FLAT"].includes(out.direction));
  assert.ok(["CONSOLIDATION","DEVELOPMENT","EXPANSION","MATURATION","TRANSITION","REVERSAL"].includes(out.phase));
  assert.match(out.structure_id,/^FS-[0-9A-F]{8}$/);
});

test("crossScaleAlignment remains bounded and read-only data shaped",async()=>{
  const desc=await phaseDescriptor(syntheticBars());
  const phaseMap={};
  for (const tf of ["1m","5m","15m","1h","4h","1d","1w"]) {
    phaseMap[tf]={...desc,phase_progress:0.5,phase_memory:{top_similarity:0.8}};
  }
  const out=crossScaleAlignment(phaseMap);
  assert.ok(out.alignment_score>=0&&out.alignment_score<=1);
  assert.equal(out.cascade_state,"COHERENT");
  assert.equal(out.pairs.length,6);
});
