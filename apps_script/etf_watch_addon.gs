/* ETF 상시 감시판: 기존 보유종목 Apps Script 프로젝트에 파일 하나로 추가.
 * ewInstall() 1회 실행: ETF 전용 5분 트리거 설치. 기존 트리거/보유종목 변경 없음.
 * ewPreview()는 읽기만 하며 메일을 발송하지 않습니다.
 * 영구 유니버스: 만료/자동삭제/주문/외부 수급 호출 없음.
 */
const EW = Object.freeze({
  SPREADSHEET_ID: '15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  SHEET: 'ETF_WATCHLIST', TZ: 'Asia/Seoul', MAX_ROWS: 99,
  HANDLER: 'ewTick5m', STATE_KEY: 'ETF_WATCH_SIGNAL_STATE_V2', CONFIRM_CYCLES: 2
});
function ewRecipient_() {
  const props = PropertiesService.getScriptProperties();
  const email = String(props.getProperty('ETF_ALERT_EMAIL') || props.getProperty('HELD_ALERT_EMAIL') || Session.getEffectiveUser().getEmail() || '').trim();
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) throw new Error('ETF_ALERT_EMAIL 또는 HELD_ALERT_EMAIL을 설정하세요.');
  return email;
}
function ewInstall() {
  ewRecipient_();
  const sh = SpreadsheetApp.openById(EW.SPREADSHEET_ID).getSheetByName(EW.SHEET);
  if (!sh) throw new Error('ETF_WATCHLIST 탭이 없습니다.');
  // First create, then remove old ETF triggers, leaving all other handlers untouched.
  const existing = ScriptApp.getProjectTriggers().filter(t => [EW.HANDLER, 'ewHourly'].indexOf(t.getHandlerFunction()) >= 0);
  ScriptApp.newTrigger(EW.HANDLER).timeBased().everyMinutes(5).create();
  existing.forEach(t => ScriptApp.deleteTrigger(t));
  console.log('ETF 5분 감시 설치 완료. 한국시간 평일 09:00~15:30 · 2회 연속 신호 확인 · 중립 제외 · 종목/신호별 하루 1회.');
}
function ewUninstall() {
  ScriptApp.getProjectTriggers().filter(t => [EW.HANDLER, 'ewHourly'].indexOf(t.getHandlerFunction()) >= 0).forEach(t => ScriptApp.deleteTrigger(t));
}
function ewNum_(x) { return typeof x === 'number' && isFinite(x) ? x : null; }
function ewFresh_(tradeTime, now) {
  return tradeTime instanceof Date && !isNaN(tradeTime.getTime()) &&
    Utilities.formatDate(tradeTime, EW.TZ, 'yyyy-MM-dd') === Utilities.formatDate(now, EW.TZ, 'yyyy-MM-dd') &&
    now.getTime() - tradeTime.getTime() >= -60000 && now.getTime() - tradeTime.getTime() <= 30*60000;
}
function ewRows_(now) {
  const sh = SpreadsheetApp.openById(EW.SPREADSHEET_ID).getSheetByName(EW.SHEET);
  if (!sh) throw new Error('ETF_WATCHLIST 탭이 없습니다.');
  const n = Math.min(EW.MAX_ROWS, Math.max(0, sh.getLastRow()-1));
  if (!n) return [];
  return sh.getRange(2,1,n,14).getValues().filter(r => r[0] === true && /^\d{6}$/.test(String(r[3]))).map(r => ({
    code:String(r[3]), name:String(r[4]), sector:String(r[2]), price:ewNum_(r[6]),
    day:ewNum_(r[7]), five:ewNum_(r[8]), twenty:ewNum_(r[9]), signal:String(r[10]),
    tradeTime:r[11], valid:r[12] === 'VALID' && ewFresh_(r[11],now) && ewNum_(r[6]) > 0
  }));
}
function ewPct_(x) { return x == null ? '—' : (x > 0 ? '+' : '')+(100*x).toFixed(2)+'%'; }
function ewEscape_(x) { return String(x).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;'); }
function ewMessage_(rows,now) {
  const sorted=rows.filter(r => r.valid).slice().sort((a,b) => (b.day == null ? -Infinity : b.day)-(a.day == null ? -Infinity : a.day));
  const pending=rows.length-sorted.length;
  const title='ETF 매수·매도 신호 '+Utilities.formatDate(now,EW.TZ,'MM-dd HH:mm');
  const plain=[title,'영역 | ETF | 1일 | 5일 | 20일 | 기술상태'];
  let html='<p>'+ewEscape_(title)+'</p><table cellpadding="5" style="border-collapse:collapse"><tr><th>영역 / ETF</th><th>1일</th><th>5일</th><th>20일</th><th>기술상태</th></tr>';
  sorted.forEach(r => {
    plain.push(r.sector+' | '+r.code+' '+r.name+' | '+ewPct_(r.day)+' | '+ewPct_(r.five)+' | '+ewPct_(r.twenty)+' | '+r.signal);
    if (typeof hmJevDisplayLines_ === 'function') hmJevDisplayLines_(r.jevGate).forEach(line => plain.push('  '+line));
    const color=/^[▲△]/.test(r.signal)?'#c00000':/^[▼▽]/.test(r.signal)?'#0044bb':'#444';
    const jev=(typeof hmJevDisplayLines_ === 'function' ? hmJevDisplayLines_(r.jevGate).join(' · ') : 'Jev: 미연결');
    html+='<tr><td>'+ewEscape_(r.sector)+' / '+ewEscape_(r.name)+'</td><td>'+ewPct_(r.day)+'<br><small>'+ewEscape_(jev)+'</small></td><td>'+ewPct_(r.five)+'</td><td>'+ewPct_(r.twenty)+'</td><td style="color:'+color+'">'+ewEscape_(r.signal)+'</td></tr>';
  });
  const note='가격 등락률 · 분배금 제외 · GOOGLEFINANCE 지연시세 · VWAP9/26 + OBV/OBV9 기술상태';
  plain.push(note,'시세 확인 대기 '+pending+'종목');
  const url='https://docs.google.com/spreadsheets/d/'+EW.SPREADSHEET_ID+'/edit#gid=2026100201';
  plain.push(url);html+='</table><p>'+note+'</p><p>시세 확인 대기 '+pending+'종목</p><a href="'+url+'">ETF 감시판 열기</a>';
  return {subject:title,body:plain.join('\n'),htmlBody:html,count:sorted.length};
}
function ewInSession_(now) {
  const dow=Utilities.formatDate(now,EW.TZ,'EEE');
  const hhmm=Number(Utilities.formatDate(now,EW.TZ,'HHmm'));
  return dow!=='Sat' && dow!=='Sun' && hhmm>=900 && hhmm<=1530;
}
function ewStage_(signal) {
  const match=String(signal).match(/^[▲△▼▽]/);
  return match ? match[0] : '';
}
// Pure state transition: two separate polling slots, one email per code/stage/day.
function ewPlan_(rows, previous, now) {
  const day=Utilities.formatDate(now,EW.TZ,'yyyy-MM-dd');
  const slot=Math.floor(now.getTime()/300000);
  const state={day:day, items:{}}; const alerts=[];
  const prior=previous && previous.day===day ? previous.items || {} : {};
  rows.forEach(r => {
    const old=prior[r.code] || {stage:'',count:0,sent:{}};
    if (old.slot===slot) { state.items[r.code]=old; return; }
    const stage=r.valid ? ewStage_(r.signal) : '';
    const count=stage && stage===old.stage ? Math.min(EW.CONFIRM_CYCLES,(old.count||0)+1) : stage ? 1 : 0;
    const next={stage:stage,count:count,slot:slot,sent:Object.assign({},old.sent||{})};
    state.items[r.code]=next;
    if (r.valid && stage && count>=EW.CONFIRM_CYCLES && !next.sent[stage] && !next.held?.[stage]) {
      alerts.push(r);
    }
  });
  return {state:state,alerts:alerts};
}
function ewApplyJevGate_(plan,now) {
  const delivered=[]; const held=[];
  (plan.alerts||[]).forEach(r => {
    const stage=ewStage_(r.signal);
    const item=plan.state.items[r.code] || (plan.state.items[r.code]={stage:stage,count:EW.CONFIRM_CYCLES,sent:{}});
    const events=[{key:'ETF_SIGNAL_'+stage,level:/^[▲▼]/.test(stage)?'STRONG':'WATCH',title:r.signal,message:r.sector+' ETF 기술신호'}];
    const cur={
      ticker:r.code,name:r.name,price:r.price,changePct:r.day,atrBand:'ETF_NA',volumePace:null,stopBroken:false,
      vwap9Dir:'ETF_NA',vwap26Dir:'ETF_NA',vwapRel:'ETF_NA',obvDir:'ETF_NA',obv9Dir:'ETF_NA',obvRel:'ETF_NA',regime:r.signal
    };

    // Cheap/direct Jev PRE-GATE first. Clearly weak upward ETF signals stop before Cloud Run.
    const preGate=(typeof hmJevPreGate_ === 'function')
      ? hmJevPreGate_(cur,events,now,'ETF')
      : {status:'NOT_CONFIGURED',decision:'PROCEED',reason:'JEV_PRE_HELPER_MISSING',needs_final_review:true};

    if (typeof hmJevPreAllowsProceed_ === 'function' && !hmJevPreAllowsProceed_(events,preGate)) {
      r.jevGate=preGate;
      // PRE-HOLD는 하루 영구차단하지 않는다. 15분 후 direct Jev만 재심사한다.
      if (preGate.reason !== 'PRE_HOLD_COOLDOWN') {
        held.push({cur:cur,events:events,jevGate:preGate});
      }
      return;
    }

    // Strong PRE-GATE proceeds directly. Uncertain survivors get the existing final Jev review.
    const gate=preGate && preGate.needs_final_review===false && typeof hmJevPromotePreGate_ === 'function'
      ? hmJevPromotePreGate_(preGate)
      : ((typeof hmJevEvaluateAlert_ === 'function') ? hmJevEvaluateAlert_(
          cur,events,
          {catalyst:{label:'ETF price/technical signal',confidence:'N/A',reason:r.sector},flow:{status:'NOT_REQUESTED'},disclosures:[],news:[]},
          now,'ETF'
        ) : {status:'NOT_CONFIGURED',decision:'SEND',reason:'HELD_JEV_HELPER_MISSING'});
    r.jevGate=gate;

    const allowed=(typeof hmJevAllowsAlert_ === 'function') ? hmJevAllowsAlert_(events,gate) : true;
    if (allowed) {
      item.sent[stage]=true; delivered.push(r);
    } else {
      item.held=Object.assign({},item.held||{}); item.held[stage]=true; held.push({cur:cur,events:events,jevGate:gate});
    }
  });
  if (held.length && typeof hmAppendJevGateLog_ === 'function') hmAppendJevGateLog_(SpreadsheetApp.openById(EW.SPREADSHEET_ID),held,now);
  return delivered;
}
function ewPreview() {
  const now=new Date(); const rows=ewRows_(now);
  const props=PropertiesService.getScriptProperties();
  const previous=JSON.parse(props.getProperty(EW.STATE_KEY)||'{}');
  const plan=ewPlan_(rows,previous,now);
  const result={session:ewInSession_(now),enabled:rows.length,valid:rows.filter(r=>r.valid).length,alertCandidates:plan.alerts.length,intervalMinutes:5,confirmationCycles:EW.CONFIRM_CYCLES};
  console.log(JSON.stringify(result)); return result;
}
function ewTick5m() {
  const now=new Date();
  if (!ewInSession_(now)) return;
  const lock=LockService.getScriptLock(); if (!lock.tryLock(1000)) return;
  try {
    const props=PropertiesService.getScriptProperties();
    const previous=JSON.parse(props.getProperty(EW.STATE_KEY)||'{}');
    const plan=ewPlan_(ewRows_(now),previous,now);
    const delivered=ewApplyJevGate_(plan,now);
    if (delivered.length) {
      const msg=ewMessage_(delivered,now);
      msg.body+='\n5분 간격 2회 연속 확인 · 동일 종목/신호 하루 1회 · 중립은 메일 제외';
      msg.htmlBody+='<p>5분 간격 2회 연속 확인 · 동일 종목/신호 하루 1회 · 중립은 메일 제외</p>';
      MailApp.sendEmail({to:ewRecipient_(),subject:msg.subject,body:msg.body,htmlBody:msg.htmlBody});
      if (typeof hmAppendJevGateLog_ === 'function') {
        hmAppendJevGateLog_(SpreadsheetApp.openById(EW.SPREADSHEET_ID),delivered.map(r => ({
          cur:{ticker:r.code,name:r.name,price:r.price,changePct:r.day,regime:r.signal},
          events:[{key:'ETF_SIGNAL_'+ewStage_(r.signal),level:/^[▲▼]/.test(ewStage_(r.signal))?'STRONG':'WATCH',title:r.signal,message:r.sector+' ETF 기술신호'}],
          jevGate:r.jevGate
        })),now);
      }
    }
    // Never mark alerts as sent before successful delivery. Failed sends retry.
    props.setProperty(EW.STATE_KEY,JSON.stringify(plan.state));
  } finally { lock.releaseLock(); }
}
