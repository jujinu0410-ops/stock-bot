'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const source=fs.readFileSync('apps_script/youtube_chartnam_clipper_v1.gs','utf8');
const runtime=fs.readFileSync('apps_script/youtube_candidate_runtime_v10.gs','utf8');
new Function(source);new Function(runtime); // Syntax validation without loading globals.

const clips=[
  ['2026-10-08','411080','샌즈랩','보안주','AI 침해','2026-10-08 08:40:00','post1','CHARTNAM_0850','2026-10-08 08:55:00'],
  ['2026-10-08','083650','비에이치아이','원전','원전 계약','2026-10-08 08:40:00','post1','CHARTNAM_0850','2026-10-08 08:55:00'],
];
let rows=[['Date','Code','Name','Theme','Reason','PublishedKST','PostId','Source','ClippedKST'],...clips];
const sheet={
  getLastRow:()=>rows.length,
  getMaxRows:()=>500,
  getRange(start,col,num,columns){return {
    getDisplayValues:()=>rows.slice(start-1,start-1+num).map(r=>r.slice(col-1,col-1+columns)),
    setValues(v){for(let i=0;i<v.length;i++)rows[start+i-1]=v[i];return this;},
    setNumberFormat:()=>this,
  }},
};
const spreadsheet={getSheetByName:n=>n==='CHARTNAM_CLIPS'?sheet:null};
const context=vm.createContext({
  console,Set,Map,Date,JSON,
  SpreadsheetApp:{getActiveSpreadsheet:()=>spreadsheet,flush:()=>{}},
  Utilities:{formatDate:(v,tz,pattern)=>pattern==='yyyy-MM-dd'?'2026-10-08':pattern==='HH:mm'?'08:51':pattern==='u'?'4':'2026-10-08 08:51:00'},
  normalizeCandidateCode_:s=>/^\d{6}$/.test(String(s))?String(s):'',
  normalizeName_:s=>String(s).toLowerCase().replace(/\s+/g,''),
  isExpiredMentionV8_:()=>false,
  collectIntelligenceCandidatesFromMailV10_:()=>({
    '411080':{name:'샌즈랩',lastSeen:'2026-10-08',dates:new Set(['2026-10-08'])},
    '005930':{name:'삼성전자',lastSeen:'2026-10-08',dates:new Set(['2026-10-08'])},
  }),
});
vm.runInContext(source,context);
const parsed=context.ycParseChartnamText_([
 '오늘의 예상 대장테마 · 종목 (10/8)',
 '[1위] 보안주(정보) (국내 이슈)',
 'AI 해킹 대응',
 '→ 샌즈랩 · 지니언스 · 안랩',
 '[주목 개별주] 테마 밖 개별 재료',
 '· 비에이치아이(083650) [신뢰 중상] 신규수주',
 '[신규상장 예정]',
 '· 아직미상장 (코스닥)',
].join('\n'));
assert.equal(parsed.length,4);
assert.equal(parsed[0].name,'샌즈랩');
assert.equal(parsed[0].theme,'보안주(정보)');
assert.equal(parsed[0].reason,'AI 해킹 대응');
assert.equal(parsed[3].explicitCode,'083650');
assert.equal(parsed[3].theme,'주목 개별주');
const combined=context.collectIntelligenceCandidates_();
assert.equal(Object.keys(combined).length,3);
assert.equal(combined['411080'].mentionDays,1);
assert.equal(combined['083650'].sourceTag,'CHARTNAM_0850');
assert.equal(combined['005930'].name,'삼성전자');

const again=context.collectIntelligenceCandidates_();
assert.equal(Object.keys(again).length,3); // duplicate code does not multiply rows
assert.equal(again['411080'].mentionDays,1);
assert.equal(context.ycParseChartnamText_('신규상장 예정\n· 기업(123456)').length,0);
console.log('PASS chartnam parser, exact code union, idempotent candidate pool; 7 assertions groups');
