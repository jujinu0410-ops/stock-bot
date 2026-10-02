/* ETF 상시 감시판: 기존 보유종목 Apps Script 프로젝트에 파일 하나로 추가.
 * ewInstall() 1회 실행: ETF 전용 시간당 트리거 설치. 기존 트리거/보유종목 변경 없음.
 * ewPreview()는 읽기만 하며 메일을 발송하지 않습니다.
 * 영구 유니버스: 만료/자동삭제/주문/외부 수급 호출 없음.
 */
const EW = Object.freeze({
  SPREADSHEET_ID: '15WgSe4yOSBqSt6YTRQEETD_Hs6J1WL2Hop9RlzFCGkw',
  SHEET: 'ETF_WATCHLIST', TZ: 'Asia/Seoul', MAX_ROWS: 99,
  HANDLER: 'ewHourly', SENT_KEY: 'ETF_WATCH_LAST_SENT_HOUR_V1'
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
  const existing = ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === EW.HANDLER);
  ScriptApp.newTrigger(EW.HANDLER).timeBased().everyHours(1).create();
  existing.forEach(t => ScriptApp.deleteTrigger(t));
  console.log('ETF 시간당 감시 설치 완료. 평일 09:20~16:20, 당일 시세가 있는 경우만 1시간 1통.');
}
function ewUninstall() {
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === EW.HANDLER).forEach(t => ScriptApp.deleteTrigger(t));
}
function ewNum_(x) { return typeof x === 'number' && isFinite(x) ? x : null; }
function ewFresh_(tradeTime, now) {
  return tradeTime instanceof Date && !isNaN(tradeTime.getTime()) &&
    Utilities.formatDate(tradeTime, EW.TZ, 'yyyy-MM-dd') === Utilities.formatDate(now, EW.TZ, 'yyyy-MM-dd');
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
  const title='ETF 상시 감시 '+Utilities.formatDate(now,EW.TZ,'MM-dd HH:mm');
  const plain=[title,'영역 | ETF | 1일 | 5일 | 20일 | 기술상태'];
  let html='<p>'+ewEscape_(title)+'</p><table cellpadding="5" style="border-collapse:collapse"><tr><th>영역 / ETF</th><th>1일</th><th>5일</th><th>20일</th><th>기술상태</th></tr>';
  sorted.forEach(r => {
    plain.push(r.sector+' | '+r.name+' | '+ewPct_(r.day)+' | '+ewPct_(r.five)+' | '+ewPct_(r.twenty)+' | '+r.signal);
    const color=/^[▲△]/.test(r.signal)?'#c00000':/^[▼▽]/.test(r.signal)?'#0044bb':'#444';
    html+='<tr><td>'+ewEscape_(r.sector)+' / '+ewEscape_(r.name)+'</td><td>'+ewPct_(r.day)+'</td><td>'+ewPct_(r.five)+'</td><td>'+ewPct_(r.twenty)+'</td><td style="color:'+color+'">'+ewEscape_(r.signal)+'</td></tr>';
  });
  const note='가격 등락률 · 분배금 제외 · GOOGLEFINANCE 지연시세 · VWAP9/26 + OBV/OBV9 기술상태';
  plain.push(note,'시세 확인 대기 '+pending+'종목');
  const url='https://docs.google.com/spreadsheets/d/'+EW.SPREADSHEET_ID+'/edit#gid=2026100201';
  plain.push(url);html+='</table><p>'+note+'</p><p>시세 확인 대기 '+pending+'종목</p><a href="'+url+'">ETF 감시판 열기</a>';
  return {subject:title,body:plain.join('\n'),htmlBody:html,count:sorted.length};
}
function ewPreview() {
  const now=new Date(); const msg=ewMessage_(ewRows_(now),now);
  console.log(msg.body); return msg.body;
}
function ewHourly() {
  const now=new Date();
  const dow=Utilities.formatDate(now,EW.TZ,'EEE');
  const hhmm=Number(Utilities.formatDate(now,EW.TZ,'HHmm'));
  if (dow==='Sat'||dow==='Sun'||hhmm<920||hhmm>1620) return;
  const lock=LockService.getScriptLock(); if (!lock.tryLock(1000)) return;
  try {
    const props=PropertiesService.getScriptProperties();
    const hour=Utilities.formatDate(now,EW.TZ,'yyyy-MM-dd-HH');
    if (props.getProperty(EW.SENT_KEY)===hour) return;
    const rows=ewRows_(now); const msg=ewMessage_(rows,now);
    // Holiday/stale data cannot generate an apparent current-day alert.
    if (!msg.count) return;
    MailApp.sendEmail({to:ewRecipient_(),subject:msg.subject,body:msg.body,htmlBody:msg.htmlBody});
    props.setProperty(EW.SENT_KEY,hour);
  } finally { lock.releaseLock(); }
}
