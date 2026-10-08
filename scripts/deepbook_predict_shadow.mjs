import fs from 'node:fs';
import { SuiGrpcClient } from '@mysten/sui/grpc';
import { predict } from '@mysten/deepbook-v3/predict';

const OUT='data/investments/deepbook_predict_shadow.json';
const LAB='data/investments/decision_lab_public.json';
const now=Date.now();
const ATM_MAX_ABS_MONEYNESS=0.001; // 10 bp; outside this range no synthetic BRs probability is emitted.

const client=new SuiGrpcClient({network:'mainnet',baseUrl:'https://fullnode.mainnet.sui.io:443'}).$extend(predict({network:'mainnet'}));
const state=fs.existsSync(OUT)?JSON.parse(fs.readFileSync(OUT,'utf8')):{schema_version:'deepbook_predict_shadow_v2',snapshots:[]};
Object.assign(state,{schema_version:'deepbook_predict_shadow_v2',mode:'shadow_research',visible_in_decision_lab:false,belief_core_authority:false,execution_authority:false,automatic_promotion:false});

function loadLab(){
  try{return fs.existsSync(LAB)?JSON.parse(fs.readFileSync(LAB,'utf8')):null;}catch{return null;}
}
function latestBelief(lab,beliefId,atMs){
  const rows=Array.isArray(lab?.forecasts)?lab.forecasts:[];
  let best=null;
  for(const r of rows){
    if(r?.belief_id!==beliefId || r?.probability==null || !r?.forecast_at) continue;
    const t=Date.parse(r.forecast_at);
    if(!Number.isFinite(t) || t>atMs) continue;
    if(!best || t>Date.parse(best.forecast_at)) best=r;
  }
  return best;
}
function clamp01(x){return Math.max(0,Math.min(1,Number(x)));}
function brsComparator(lab,{strike,referencePrice,forward,expiryMs,observedAtMs}){
  const trend=latestBelief(lab,'btc.trend.bullish',observedAtMs);
  if(!trend) return {status:'NO_BRS_PRIOR',methodology_version:'brs-btc-same-contract-v1',production_write_authority:false,automatic_promotion:false};
  const anchor=Number(referencePrice ?? forward);
  const k=Number(strike);
  const moneyness=(Number.isFinite(anchor)&&anchor>0&&Number.isFinite(k))?(k-anchor)/anchor:null;
  const applicable=moneyness!=null && Math.abs(moneyness)<=ATM_MAX_ABS_MONEYNESS;
  const ctx={};
  for(const id of ['btc.volatility.benign','btc.usd_environment.supportive','btc.liquidity.supportive']){
    const r=latestBelief(lab,id,observedAtMs);
    if(r) ctx[id]={probability:clamp01(r.probability),forecast_at:r.forecast_at,horizon_hours:r.horizon_hours??null};
  }
  return {
    status:applicable?'FROZEN_AT_T0':'NOT_APPLICABLE_NON_ATM',
    methodology_version:'brs-btc-same-contract-v1',
    target:'BTC_above_strike_at_exact_deepbook_expiry',
    probability:applicable?clamp01(trend.probability):null,
    source_belief_id:'btc.trend.bullish',
    source_forecast_at:trend.forecast_at,
    source_horizon_hours:trend.horizon_hours??null,
    source_age_ms:Math.max(0,observedAtMs-Date.parse(trend.forecast_at)),
    contract_expiry_ms:Number(expiryMs),
    contract_horizon_ms:Number(expiryMs)-observedAtMs,
    strike:k,
    anchor_price:Number.isFinite(anchor)?anchor:null,
    moneyness,
    applicability_rule:'abs((strike-anchor)/anchor) <= 0.001',
    context:ctx,
    production_write_authority:false,
    belief_core_writeback:false,
    execution_authority:false,
    automatic_promotion:false
  };
}

try {
 const lab=loadLab();
 const markets=(await client.predict.read.markets()).filter(m=>Number(m.expiryMs)>now+5000&&!m.mintPaused);
 const quotes=[];
 for(const m of markets){
   try{
     const p=await client.predict.read.pricer({underlying:'BTC',expiryMs:m.expiryMs});
     const strike=m.referencePrice??Math.round(p.forward/m.admissionTickSize)*m.admissionTickSize;
     const observedAtMs=Date.now();
     const comparator=brsComparator(lab,{strike,referencePrice:m.referencePrice,forward:p.forward,expiryMs:m.expiryMs,observedAtMs});
     quotes.push({
       asset:'BTC',
       market_id:m.id,
       expiry_ms:Number(m.expiryMs),
       horizon_ms:Number(m.expiryMs)-observedAtMs,
       strike,
       reference_price:m.referencePrice,
       forward:p.forward,
       up_probability:p.up(strike),
       down_probability:p.down(strike),
       observed_at:new Date(observedAtMs).toISOString(),
       as_of:p.asOf,
       source:'deepbook_predict_mainnet_sdk',
       independence_cluster:'deepbook_predict_market',
       brs_same_contract:comparator
     });
   }catch(e){
     quotes.push({asset:'BTC',market_id:m.id,expiry_ms:Number(m.expiryMs),observed_at:new Date().toISOString(),error:String(e?.message||e),source:'deepbook_predict_mainnet_sdk'});
   }
 }
 state.source_status=quotes.some(x=>x.up_probability!=null)?'ok':'no_priceable_market';
 delete state.last_error;
 state.updated_at=new Date().toISOString();
 state.same_contract_shadow={
   enabled:true,
   methodology_version:'brs-btc-same-contract-v1',
   frontend_visible:false,
   extra_network_calls:0,
   note:'BRs comparator reuses local Decision LAB BTC beliefs only; no additional market/API fetches.'
 };
 state.snapshots.push({captured_at:state.updated_at,quotes});
 state.snapshots=state.snapshots.slice(-5000);
}catch(e){
 state.source_status='error';
 state.updated_at=new Date().toISOString();
 state.last_error=String(e?.message||e);
}
fs.writeFileSync(OUT,JSON.stringify(state,null,2)+'\n');
console.log(JSON.stringify({status:state.source_status,latest:state.snapshots?.at(-1)?.quotes?.length||0,same_contract_shadow:true}));
if(state.source_status==='error')process.exitCode=1;
