import test from 'node:test';
import assert from 'node:assert/strict';
import '../scripts/eurusd-cb-risk-public.js';
const now=Date.parse('2026-10-28T18:00:00Z');
test('missing/stale data never displays current CLOSE or fill',()=>{
  const row={action:'CLOSE_BEFORE_EVENT',evaluated_at:'2026-10-28T17:00:00Z',quote_verified:true};
  assert.match(globalThis.BriefRoomsEurusdCbRisk.view(row,true,now),/stale/);
  assert.doesNotMatch(globalThis.BriefRoomsEurusdCbRisk.view(row,true,now),/Close before event/);
  assert.match(globalThis.BriefRoomsEurusdCbRisk.view(null,false,now),/niedostępna/);
});
test('SHADOW and uncertainty remain explicit in both languages',()=>{
  const row={action:'PROTECT_PROFIT',evaluated_at:'2026-10-28T18:00:00Z',diagnostics:{wire_health:'PARTIAL'}};
  for(const en of [false,true]) assert.match(globalThis.BriefRoomsEurusdCbRisk.view(row,en,now),/SHADOW/);
  assert.match(globalThis.BriefRoomsEurusdCbRisk.view(row,true,now),/No verified executable quote/);
});
test('untrusted source and injected headline never become HTML',()=>{
  const row={evaluated_at:'2026-10-28T18:00:00Z',diagnostics:{latest_official_document:'javascript:alert(1)',wire_health:'<script>evil</script>'}};
  const html=globalThis.BriefRoomsEurusdCbRisk.view(row,true,now);
  assert.doesNotMatch(html,/<script>|javascript:/);
  assert.match(html,/&lt;script&gt;/);
});
