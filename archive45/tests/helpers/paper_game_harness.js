const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path'),crypto=require('node:crypto');
const root=path.resolve(__dirname,'../..'),source=path.join(root,'apps_script/paper_game'),baseline=path.join(__dirname,'../fixtures/paper_game_baseline');
class Sheet{
  constructor(rows=[]){this.rows=structuredClone(rows);this.fail=null;}
  getLastRow(){let n=this.rows.length;while(n&&!this.rows[n-1].some(x=>x!==''&&x!=null))n--;return n;}
  getMaxRows(){return 10000;}insertRowsAfter(){}
  getRange(row,col,n=1,m=1){
    if(typeof row==='string'){const a=row.match(/^([A-Z]+)(\d+):([A-Z]+)(\d+)$/);if(!a)throw Error('TEST_BAD_A1');col=a[1].charCodeAt(0)-64;m=a[3].charCodeAt(0)-a[1].charCodeAt(0)+1;row=Number(a[2]);n=Number(a[4])-row+1;}
    const self=this;return {
    getValues(){return Array.from({length:n},(_,i)=>Array.from({length:m},(_,j)=>self.rows[row+i-1]?.[col+j-1]??''));},
    getDisplayValues(){return this.getValues().map(r=>r.map(String));},
    setValues(values){if(self.fail){const e=self.fail;self.fail=null;throw Error(e);}assert.equal(values.length,n);values.forEach((r,i)=>{assert.equal(r.length,m);self.rows[row+i-1]??=[];r.forEach((v,j)=>self.rows[row+i-1][col+j-1]=v);});},
    clearContent(){for(let i=0;i<n;i++)for(let j=0;j<m;j++){self.rows[row+i-1]??=[];self.rows[row+i-1][col+j-1]='';}},
    setValue(v){this.setValues([[v]]);},getValue(){return this.getValues()[0][0];}
  };}
}
function context(legacy=false){
  const props={},sheets={},calls={fetch:0,mail:0,locks:0},logs=[];
  const book={getSheetByName(n){return sheets[n]||null;},insertSheet(n){return sheets[n]=new Sheet();}};
  const c=vm.createContext({Date,Set,Map,console:{log(){},error(...x){logs.push(x.join(' '));}},
    PropertiesService:{getScriptProperties:()=>({getProperty:k=>props[k]??null,setProperty(k,v){props[k]=v;},deleteProperty(k){delete props[k];}})},
    SpreadsheetApp:{openById:()=>book},LockService:{getScriptLock:()=>({tryLock(){calls.locks++;return true;},releaseLock(){}})},
    Utilities:{formatDate(d,tz,fmt){const s=new Date(d.getTime()+9*3600000).toISOString();return fmt==='yyyy-MM-dd'?s.slice(0,10):fmt==='HH'?s.slice(11,13):fmt==='mm'?s.slice(14,16):s;},DigestAlgorithm:{SHA_256:'SHA_256'},Charset:{UTF_8:'UTF_8'},computeDigest(_,s){return [...crypto.createHash('sha256').update(s).digest()];}},
    UrlFetchApp:{fetch(){calls.fetch++;throw Error('NO_NETWORK');}},ScriptApp:{getOAuthToken(){return 'TEST_TOKEN';}},
    GmailApp:{search(){calls.mail++;return [];}},UM:{BOOK:'monitor'},hmAlertRecipient_:()=> 'self@example.test'});
  for(const name of ['PaperGame.js','PaperGameD.js'])vm.runInContext(fs.readFileSync(path.join(legacy?baseline:source,name),'utf8'),c);
  if(!legacy)vm.runInContext(fs.readFileSync(path.join(source,'PaperGameE45.js'),'utf8'),c);
  return {c,props,sheets,calls,logs,book};
}
const plain=x=>JSON.parse(JSON.stringify(x)),dt=(day,t)=>new Date(day+'T'+t+':00+09:00'),day='2026-11-04',cfg={enabled:true,start:day,end:'2026-11-30'};
function obs(code,round,at,price=10000,close=10100,d=day){
  return {id:'OBS|'+d+'|'+round+'|'+code,day:d,kind:'OBS',at:dt(d,at).toISOString(),data:{code,round,ok:true,price,source:'KIWOOM_KA10001',paper:{quote_received_at:dt(d,at).toISOString(),computed_at:dt(d,at).toISOString(),quality:'VALID',first45:{completed:true,quality:'VALID',start:dt(d,'09:00').toISOString(),end:dt(d,'09:45').toISOString(),open:10000,high:10200,low:9800,close,volume:1000}}}};
}
function forecast(direction='UP',received='08:00',at='09:02',d=day){return {id:'FORECAST|'+d,day:d,kind:'FORECAST',at:dt(d,at).toISOString(),data:{day:d,direction,source_id:'original-mail',received_at:dt(d,received).toISOString(),sha256:'a'.repeat(64)}};}
function closing(code,price=10000,d=day,observedDay=d){return {id:'CLOSE|'+d+'|'+code,day:d,kind:'CLOSE',at:dt(observedDay,'16:12').toISOString(),data:{ticker:code,day:d,close:price,quality:'CONFIRMED_DAILY_CLOSE',source:'NAVER_DAILY_AFTER_1610',observed_at:dt(observedDay,'16:11').toISOString(),market_time:dt(d,'15:30').toISOString(),paper_fill:true}};}
function inputs(direction='UP',barClose=10100,d=day){return [forecast(direction,'08:00','09:02',d),obs('069500','r1','09:48',10000,barClose,d),obs('114800','r1','09:48',10000,10000,d),obs('069500','r2','09:53',10000,barClose,d),obs('114800','r2','09:53',10000,10000,d)];}
const compute=(h,events,now=dt(day,'16:20'),config=cfg)=>plain(h.c.e45Compute_(events,now,config));

module.exports={test,assert,fs,vm,path,crypto,root,source,baseline,Sheet,context,plain,dt,day,cfg,obs,forecast,closing,inputs,compute};
