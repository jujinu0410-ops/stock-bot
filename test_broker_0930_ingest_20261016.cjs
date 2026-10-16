const fs=require('fs'),vm=require('vm'),assert=require('assert');
const src=fs.readFileSync(__dirname+'/apps_script/youtube_candidate_two_mails_v12.gs','utf8');
new vm.Script(src);
const date='2026-10-16',msgs=[],state={},liveRows=[];
let writes=0,blockWrite=false,searches=[];
const mk=(id,hh,mm,stocks,subject='[경제 Intelligence] [08:45 증권사 리포트 종목수집] 2026-10-16 · 5종목')=>({
 id,subject,date:new Date(Date.UTC(2026,9,16,hh-9,mm)),
 body:'08:45 증권사 리포트 종목수집 완료\n[YT_ALL_STOCKS_BEGIN]\n'+stocks.join('\n')+'\n[YT_ALL_STOCKS_END]'});
msgs.push(mk('before',8,29,['000001|테스트']));
msgs.push(mk('latest',9,15,['003230|삼양식품','006260|LS','440110|파두','028050|삼성E&A','006360|GS건설']));
msgs.push(mk('boundary',9,30,['005930|삼성전자']));
msgs.push(mk('late',9,31,['000002|테스트']));
msgs.push(mk('stale',9,18,['000003|테스트'],'[경제 Intelligence] [08:45 증권사 리포트 종목수집] 2026-10-15 · 1종목'));
msgs.push(mk('expert',8,50,['000004|테스트'],'[경제 Intelligence] [08:50 전문가 종목수집] 2026-10-16 · 1종목'));
const props={getProperty:k=>state[k]||null,setProperty:(k,v)=>{state[k]=String(v)},deleteProperty:k=>{delete state[k]}};
const context={console,Date,Map,Set,Logger:{log(){}},
 YC:{CANDIDATE_SHEET:'CANDIDATES',DATA_START_ROW:2},
 normalizeCandidateCode_:s=>/^\d{6}$/.test(String(s))?String(s):'',
 todayKeyV8_:()=>date,ymdFromAnyV8_:s=>String(s||''),
 isExpiredMentionV8_:()=>false,reclaimExpiredRowsV8_:()=>{},
 seoulNowPartsV5_:()=>({dateKey:date,minuteOfDay:9*60+25,weekday:5}),
 isWeekdayV5_:()=>true,
 isWithinMinuteWindowV5_:(p,a,b)=>p.minuteOfDay>=a&&p.minuteOfDay<=b,
 PropertiesService:{getScriptProperties:()=>props},
 SpreadsheetApp:{flush(){},getActiveSpreadsheet(){return {getSheetByName:()=>({
 getLastRow:()=>liveRows.length+1,
 getRange:()=>({getDisplayValues:()=>liveRows})})}}},
 Utilities:{formatDate:(dt,tz,f)=>{
 const t=new Date(dt.getTime()+9*3600000);
 return f==='yyyy-MM-dd'?t.toISOString().slice(0,10):
 f==='HH'?String(t.getUTCHours()).padStart(2,'0'):
 f==='mm'?String(t.getUTCMinutes()).padStart(2,'0'):''}},
 GmailApp:{search(q){searches.push(q);return msgs.map(m=>({getMessages:()=>[{
 getSubject:()=>m.subject,getPlainBody:()=>m.body,getDate:()=>m.date,getId:()=>m.id
 }]}));}},
 refreshYouTubeCandidatePoolV2(){writes++;if(!blockWrite){const pool=context.collectIntelligenceCandidates_();
 Object.keys(pool).forEach(code=>{
 const r=liveRows.find(r=>r[3]===code);
 if(r){r[0]='Y';r[5]=date;}else liveRows.push(['Y',pool[code].name,'KOSPI',code,'',date,'2026-11-16']);
 });}state.LAST_CANDIDATE_REFRESH_AT=String(writes);}
};
vm.createContext(context);vm.runInContext(src,context);
assert.equal(vm.runInContext('YCV12.BROKER_END_EXCLUSIVE',context),9*60+31);
const mails=context.ycv12TodayMails_();const ids=mails.map(m=>m.id);
assert.deepEqual(ids.sort(),['boundary','latest']);
assert.equal(searches.length,2);
assert.deepEqual(context.ycv12ParseBlock_(msgs[1].body).stocks.map(r=>r.code),
 ['003230','006260','440110','028050','006360']);
blockWrite=true;
assert.throws(()=>context.ycv12MaybeCollect_(),/TWO_MAIL_V12_UNCONFIRMED_/);
assert(!props.getProperty('TWO_MAIL_V12_PROCESSED_IDS'));
blockWrite=false;
const status=context.ycv12MaybeCollect_();
assert(status.includes('BROKER=2 mails/6 stocks'),status);
assert.equal(writes,2);
assert.equal(liveRows.length,6);
assert(liveRows.every(r=>r[0]==='Y'&&r[5]===date));
assert.equal(context.ycv12MaybeCollect_(),'SKIP_POLL_THROTTLE');
delete state.TWO_MAIL_V12_LAST_POLL_MS;
assert(context.ycv12MaybeCollect_().includes('NO_NEW_SOURCE_MAIL'));
assert.equal(writes,2);
console.log('PASS 09:15 and 09:30 accepted; 08:29/09:31 rejected; date in subject checked; 08:50 excluded; separate Gmail search + id dedup; every ticker verified on-sheet; failed write retried; no duplicate ingestion');
