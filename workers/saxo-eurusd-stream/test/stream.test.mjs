import {test} from "node:test";
import assert from "node:assert/strict";
import {parseSaxoBinaryMessages,projectQuote,quoteFresh} from "../src/index.mjs";

function frame(referenceId, payload, id=11n) {
  const ref = Buffer.from(referenceId,"ascii");
  const body = Buffer.from(JSON.stringify(payload));
  const b = Buffer.alloc(16+ref.length+body.length);
  b.writeBigUInt64LE(id,0); b.writeUInt8(ref.length,10);
  ref.copy(b,11); b.writeUInt8(0,11+ref.length);
  b.writeUInt32LE(body.length,12+ref.length);
  body.copy(b,16+ref.length);return b;
}
test("Saxo little-endian binary frames can contain more than one message",()=>{
  const a=frame("EURUSD_SPOT_21",{Quote:{Bid:1.1,Ask:1.101}},7n);
  const b=frame("_heartbeat",{Heartbeats:[]},9n);
  const msgs=parseSaxoBinaryMessages(Buffer.concat([a,b]));
  assert.equal(msgs.length,2);
  assert.equal(msgs[0].messageId,"7");
  assert.equal(msgs[1].referenceId,"_heartbeat");
});
test("incomplete, malformed, unsupported and oversized frames fail closed",()=>{
  const valid=frame("EURUSD_SPOT_21",{Quote:{Bid:1.1,Ask:1.101}});
  assert.throws(()=>parseSaxoBinaryMessages(valid.subarray(0,12)));
  assert.throws(()=>parseSaxoBinaryMessages(valid.subarray(0,valid.length-1)));
  const invalid=Buffer.from(valid);invalid[11+15]=1; // message format byte
  assert.throws(()=>parseSaxoBinaryMessages(invalid));
  assert.throws(()=>parseSaxoBinaryMessages(Buffer.alloc(70000)));
});
test("snapshot plus partial BID updates retain independent ask freshness",()=>{
  const start=Date.UTC(2026,9,29,12,0,0);
  let q=projectQuote(null,{Quote:{
    Bid:1.1201,Ask:1.1203,DelayedByMinutes:0,ErrorCode:"None",
    PriceTypeBid:"Indicative",PriceTypeAsk:"Indicative"
  }},start);
  assert.ok(quoteFresh(q,start+14000));
  q=projectQuote(q,{Quote:{Bid:1.12015}},start+10000);
  assert.equal(q.raw.Ask,1.1203);
  assert.equal(q.bidAt,start+10000);
  assert.equal(q.askAt,start);
  assert.equal(quoteFresh(q,start+16000),false);
});
test("heartbeat and non-price metadata cannot refresh a quote",()=>{
  const now=Date.UTC(2026,9,29,12,0,0);
  const q=projectQuote(null,{Quote:{Bid:1.1201,Ask:1.1202,DelayedByMinutes:0}},now);
  assert.equal(projectQuote(q,{Heartbeats:[]},now+10000),null);
  const metadata=projectQuote(q,{Quote:{SomeOtherField:"x"}},now+14000);
  assert.equal(metadata.bidAt,now);
  assert.equal(metadata.askAt,now);
  assert.equal(quoteFresh(metadata,now+15001),false);
});
test("delayed, inverted, too-wide and old indicative prices are rejected",()=>{
  const t=Date.now();
  const base={Bid:1.12,Ask:1.1202,DelayedByMinutes:0};
  for(const bad of [
    {...base,DelayedByMinutes:1}, {...base,Ask:1.11},
    {...base,Ask:1.123},{...base,PriceTypeBid:"OldIndicative"}
  ]) assert.equal(projectQuote(null,{Quote:bad},t),null);
});
