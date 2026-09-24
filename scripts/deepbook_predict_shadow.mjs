import fs from 'node:fs';
import { SuiGrpcClient } from '@mysten/sui/grpc';
import { predict } from '@mysten/deepbook-v3/predict';
const OUT='data/investments/deepbook_predict_shadow.json', now=Date.now();
const client=new SuiGrpcClient({network:'mainnet',baseUrl:'https://fullnode.mainnet.sui.io:443'}).$extend(predict({network:'mainnet'}));
const state=fs.existsSync(OUT)?JSON.parse(fs.readFileSync(OUT,'utf8')):{schema_version:'deepbook_predict_shadow_v2',snapshots:[]};
Object.assign(state,{mode:'shadow_research',visible_in_decision_lab:false,belief_core_authority:false,execution_authority:false,automatic_promotion:false});
try {
 const markets=(await client.predict.read.markets()).filter(m=>Number(m.expiryMs)>now+5000&&!m.mintPaused);
 const quotes=[];
 for(const m of markets){try{const p=await client.predict.read.pricer({underlying:'BTC',expiryMs:m.expiryMs});const strike=m.referencePrice??Math.round(p.forward/m.admissionTickSize)*m.admissionTickSize;quotes.push({asset:'BTC',market_id:m.id,expiry_ms:Number(m.expiryMs),horizon_ms:Number(m.expiryMs)-now,strike,reference_price:m.referencePrice,forward:p.forward,up_probability:p.up(strike),down_probability:p.down(strike),observed_at:new Date().toISOString(),as_of:p.asOf,source:'deepbook_predict_mainnet_sdk',independence_cluster:'deepbook_predict_market'});}catch(e){quotes.push({asset:'BTC',market_id:m.id,expiry_ms:Number(m.expiryMs),observed_at:new Date().toISOString(),error:String(e?.message||e),source:'deepbook_predict_mainnet_sdk'});}}
 state.source_status=quotes.some(x=>x.up_probability!=null)?'ok':'no_priceable_market';state.updated_at=new Date().toISOString();state.snapshots.push({captured_at:state.updated_at,quotes});state.snapshots=state.snapshots.slice(-5000);
}catch(e){state.source_status='error';state.updated_at=new Date().toISOString();state.last_error=String(e?.message||e);}
fs.writeFileSync(OUT,JSON.stringify(state,null,2)+'\n');console.log(JSON.stringify({status:state.source_status,latest:state.snapshots?.at(-1)?.quotes?.length||0}));if(state.source_status==='error')process.exitCode=1;
