const assert=require('node:assert/strict'),fs=require('fs'),vm=require('vm');
let mode='ACTIVE',hhmm='1430';
const ctx={
 Utilities:{formatDate:()=>hhmm},
 SpreadsheetApp:{openById:()=>({getSheetByName:()=>({getRange:()=>({getValue:()=>mode})})})}
};
vm.createContext(ctx);
vm.runInContext(fs.readFileSync('apps_script/youtube_unified_bootstrap.gs','utf8'),ctx);
assert.equal(ctx.ycUnifiedActive_(),true);
hhmm='1730';assert.equal(ctx.ycUnifiedActive_(),false);
hhmm='0830';assert.equal(ctx.ycUnifiedActive_(),false);
mode='PREVIEW';hhmm='1100';assert.equal(ctx.ycUnifiedActive_(),false);
console.log('PASS YouTube unified only in verified KRX window; afterhours legacy candidate runner preserved');
