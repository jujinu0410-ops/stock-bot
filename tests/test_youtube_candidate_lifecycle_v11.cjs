const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const root = path.resolve(__dirname, '..');
const runtime = fs.readFileSync(path.join(root, 'apps_script/youtube_candidate_lifecycle_v11.gs'), 'utf8');
const normalize = v => {
  const s = String(v || '').replace(/^(KRX:|KOSDAQ:|A)/,'').trim();
  return /^\d{1,6}$/.test(s) ? s.padStart(6,'0') : '';
};
const context = {refreshYouTubeCandidatePoolV2:()=> 'BASE_REFRESH', normalizeCandidateCode_:normalize};
const headerCount = vm.runInNewContext(runtime + '\nYCV11.HEADERS.length', context);
assert.equal(headerCount, 24, 'history ledger must have 24 aligned columns');

const sessions = [
  '2026-10-01','2026-10-02','2026-10-05','2026-10-06','2026-10-07',
  '2026-10-08','2026-10-12','2026-10-13','2026-10-14','2026-10-15',
  '2026-10-16','2026-10-19','2026-10-20','2026-10-21','2026-10-22',
  '2026-10-23','2026-10-26','2026-10-27','2026-10-28','2026-10-29'
];
assert.equal(context.ycv11AgeFromSessions_('2026-10-01', sessions), 20);
assert.equal(context.ycv11AgeFromSessions_('2026-10-08', sessions), 15);
assert.equal(context.ycv11AgeFromSessions_('2026-10-12', sessions), 14);
assert.equal(context.ycv11AgeFromSessions_('2026-10-29', sessions), 1);
assert.equal(context.ycv11AgeFromSessions_('2026-10-30', sessions), 0,
  'non-session mention date must begin counting on the next KRX session');

assert.equal(context.ycv11LifecycleStateFromAge_(0), 'ACTIVE');
assert.equal(context.ycv11LifecycleStateFromAge_(9), 'ACTIVE');
assert.equal(context.ycv11LifecycleStateFromAge_(10), 'OBSERVATION');
assert.equal(context.ycv11LifecycleStateFromAge_(19), 'OBSERVATION');
assert.equal(context.ycv11LifecycleStateFromAge_(20), 'EXPIRED');

assert.equal(context.ycv11TargetSession_('2026-10-01',10,sessions), '2026-10-15');
assert.equal(context.ycv11TargetSession_('2026-10-01',20,sessions), '2026-10-29');
assert.equal(context.ycv11TargetSession_('2026-10-29',20,sessions), '',
  'future checkpoint is not invented before the session exists');

assert.equal(context.ycv11EventKey_('660','2026-10-29'), '000660|2026-10-29');
assert.equal(context.ycv11EventKey_('000660','2026-10-29'), '000660|2026-10-29');
assert.notEqual(context.ycv11EventKey_('000660','2026-10-29'),
  context.ycv11EventKey_('000660','2026-10-30'),
  'a later re-mention must create a separate evaluation event');

const baseCaptureContext = {normalizeCandidateCode_:normalize};
const capturedBase = vm.runInNewContext(
  "function refreshYouTubeCandidatePoolV2(){return 'BASE_REFRESH';}\n" +
  runtime + "\nycv11BaseRefreshV8_()",
  baseCaptureContext
);
assert.equal(capturedBase, 'BASE_REFRESH',
  'V11 wrapper must preserve and call the V8 refresh implementation, not recurse');

const unified = fs.readFileSync(path.join(root, 'apps_script/paper_game/UnifiedWatchlists30.js'), 'utf8');
const activeFlag = unified.match(/function umOn_M30\(x\)\s*\{[^}]+\}/);
assert.ok(activeFlag, 'live universe must expose its active flag gate');
assert.equal(vm.runInNewContext(activeFlag[0] + '\numOn_M30("Y")', {}), true);
assert.equal(vm.runInNewContext(activeFlag[0] + '\numOn_M30("E")', {}), false,
  'observation-only rows must be excluded from the live signal universe');
assert.equal(vm.runInNewContext(activeFlag[0] + '\numOn_M30("N")', {}), false);


function makeTestContext(candidateRow) {
  const ctx = {
    refreshYouTubeCandidatePoolV2:()=> 'BASE_REFRESH',
    normalizeCandidateCode_:normalize,
    ymdFromAnyV8_:v=>String(v || '').slice(0,10),
    todayKeyV8_:()=> '2026-11-10',
    YC:{CANDIDATE_SHEET:'CANDIDATES',DATA_START_ROW:2},
    SpreadsheetApp:{
      getActiveSpreadsheet:()=>({
        getSheetByName:()=>candidateSheet
      }),
      flush:()=>{}
    },
    PropertiesService:{getScriptProperties:()=>({setProperty:()=>{}})},
    Utilities:{formatDate:()=> '2026-11-10 08:00:00'},
    Logger:{log:()=>{}}
  };
  const candidateSheet = {
    getLastRow:()=>2,
    getRange:()=>({getValues:()=>[candidateRow]})
  };
  const historySheet = {
    getLastRow:()=>1,
    getRange:()=>({getDisplayValues:()=>[]})
  };
  vm.runInNewContext(runtime,ctx);
  ctx.ycv11GetSessions_=()=>sessions;
  ctx.ycv11CompletedSessions_=()=>sessions;
  ctx.ycv11EnsureHistorySheet_=()=>historySheet;
  ctx.reclaimExpiredRowsV8_=()=> 'MIGRATION_TEST';
  return {ctx,historySheet};
}

let migratedRows = [];
const legacy = makeTestContext([
  'Y','SK하이닉스','KOSPI','000660','KRX:000660','2026-10-29','2026-11-29',100000
]);
legacy.ctx.ycv11AppendRows_=(sheet,rows)=>{migratedRows=rows;};
vm.runInNewContext('ycv11MigrateLegacyHistoryV11_()',legacy.ctx);
assert.equal(migratedRows.length,1);
assert.equal(migratedRows[0].length,24,'legacy migration row must align to all 24 columns');
assert.equal(migratedRows[0][18],'ACTIVE_LEGACY');

let freshRows = [];
const fresh = makeTestContext([
  'Y','SK하이닉스','KOSPI','000660','KRX:000660','2026-11-10','2026-12-10',100000
]);
fresh.ctx.ycv11AppendRows_=(sheet,rows)=>{freshRows=rows;};
vm.runInNewContext(
  "ycv11RegisterFreshMentionsV11_([{date:'2026-11-10',type:'REGULAR',stocks:[{code:'000660'}]}," +
  "{date:'2026-11-10',type:'BROKER',stocks:[{code:'000660'}]}])",
  fresh.ctx
);
assert.equal(freshRows.length,1,'same-day duplicate sources must create one mention event');
assert.equal(freshRows[0].length,24,'new mention row must align to all 24 columns');
assert.equal(freshRows[0][4],'REGULAR+BROKER','same-day sources must merge');
assert.equal(freshRows[0][5],100000,'mention-time baseline price must be captured');
assert.equal(freshRows[0][18],'ACTIVE');


const perfSessions = [];
let perfDate = new Date('2026-07-01T00:00:00Z');
while (perfSessions.length < 60) {
  const day = perfDate.getUTCDay();
  if (day !== 0 && day !== 6) perfSessions.push(perfDate.toISOString().slice(0,10));
  perfDate.setUTCDate(perfDate.getUTCDate()+1);
}
const mentionIndex = 20;
const mentionDate = perfSessions[mentionIndex];
const perfBars = perfSessions.map((date,i)=>({date,close:1000+i}));
let perfRow = [
  '000660|' + mentionDate,'000660','SK하이닉스',mentionDate,'REGULAR',
  1000,perfSessions[mentionIndex-1],'2026-07-01 08:00:00',
  '', '', '', '', '', '', '', '', '', '',
  'ACTIVE','',0,'',0,''
];
const perfContext = {
  refreshYouTubeCandidatePoolV2:()=> 'BASE',
  normalizeCandidateCode_:normalize
};
vm.runInNewContext(runtime,perfContext);
perfContext.ycv11ParseFchart_=()=>perfBars;
const perfHistory = {
  getLastRow:()=>2,
  getRange:()=>({
    getValues:()=>[perfRow],
    setValues:v=>{perfRow=v[0];}
  })
};
const perfResult = perfContext.ycv11ProcessDueEvaluationsV11_(perfHistory,perfSessions);
assert.equal(perfRow.length,24);
assert.equal(perfRow[8],perfSessions[mentionIndex+9],'D+10 must use the 10th KRX session');
assert.equal(perfRow[13],perfSessions[mentionIndex+19],'D+20 must use the 20th KRX session');
assert.equal(perfRow[9],perfBars[mentionIndex+9].close);
assert.equal(perfRow[14],perfBars[mentionIndex+19].close);
assert.ok(Number(perfRow[11])>0 && Number(perfRow[16])>0,'D+10 and D+20 MA20 snapshots must be captured');
assert.equal(perfRow[18],'COMPLETED');
assert.equal(perfRow[20],1,'D+10 attempts are tracked separately');
assert.equal(perfRow[22],1,'D+20 attempts are tracked separately');
assert.match(perfResult,/EVALUATED=2/);

console.log('PASS: 10/20 trading-day boundaries, KRX-session counting, separate re-mention events, 24-column ledger, D+10/D+20 MA20 evaluation, V8 wrapper preservation, and E-state exclusion.');
