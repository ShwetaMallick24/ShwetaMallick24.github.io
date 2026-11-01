import assert from 'node:assert/strict';
import {metrics,score,daily,STYLES} from '../core.mjs';
const now=Date.UTC(2026,10,1),day=86400,start=now/1000-101*day;
const candles=Array.from({length:101},(_,i)=>[start+i*day,90,110,100,100*Math.exp(i*.001),10]);
const record={stats:{open:100,last:110,high:111,low:99,volume:1e6},candles,at:new Date(now).toISOString()};
const m=metrics(record,record,now);
assert.equal(m.full,true);assert.ok(Math.abs(m.ret30-(Math.exp(.03)-1)*100)<1e-8);assert.equal(m.relative,0);assert.equal(m.drawdown,0);assert.ok(m.vol<1e-10);
for(const key of Object.keys(STYLES)){assert.equal(STYLES[key].weights.reduce((a,b)=>a+b),100);assert.ok(score(m,key).value>=0&&score(m,key).value<=100)}
const gap={...record,candles:candles.filter((_,i)=>i!==90)};assert.equal(metrics(gap,record,now).full,false);assert.equal(score(metrics(gap,record,now)),null);
const pending=[...candles,[now/1000,1,2,1,99999,5]];assert.equal(daily(pending,now).length,101);
const ddRecord={...record,candles:candles.map((x,i)=>[...x.slice(0,4),i===100?50:100,10])};assert.equal(metrics(ddRecord,record,now).drawdown,50);
console.log('Verified return windows, matching benchmark, volatility, drawdown, missing-data handling, incomplete-day exclusion, and weight totals.');
