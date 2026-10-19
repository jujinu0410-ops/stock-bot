/*
 * Runtime V3 candidate parser override.
 *
 * Purpose: make Gmail candidate parsing deterministic for the current
 * Economic Intelligence mail format.  We deliberately key off the exact
 * candidate-section phrase first and then accept only explicit six-digit
 * ticker cards.  This avoids accidentally selecting an earlier/later
 * narrative heading that also contains the words "언급 종목".
 */

function collectIntelligenceCandidates_() {
  const query = 'subject:"경제 Intelligence" newer_than:' + YCI.LOOKBACK_DAYS + 'd -in:trash -in:spam';
  const threads = GmailApp.search(query, 0, YCI.MAX_THREADS);
  const byCode = {};
  let structuredMailCount = 0;
  let parsedCardCount = 0;

  threads.forEach(function(thread) {
    thread.getMessages().forEach(function(message) {
      const subject = String(message.getSubject() || '');
      if (subject.indexOf(YCI.SUBJECT_PREFIX) !== 0) return;

      const body = String(message.getPlainBody() || '')
        .replace(/\r\n/g, '\n')
        .replace(/\r/g, '\n')
        .replace(/[\u00a0\u200b\ufeff\u200e\u200f]/g, ' ')
        .replace(/[\u202a-\u202e\u2066-\u2069]/g, '');

      // Prefer the exact live heading phrase.  Do not choose the last generic
      // "언급 종목" heading because the report contains other narrative tables.
      let markerPos = body.lastIndexOf('오늘 언급·주목 종목');
      if (markerPos < 0) markerPos = body.lastIndexOf('언급·주목 종목');
      if (markerPos < 0) return;

      structuredMailCount += 1;
      const section = body.substring(markerPos);
      const seenThisMessage = new Set();

      // Current vertical card form:
      // 삼성전기
      // (009150)
      // [직접 언급]
      //
      // The explicit six-digit code is canonical; the tag line is not required.
      const pairRe = /(?:^|\n)\s*([^\n\r]{2,50}?)\s*\n\s*\(\s*(\d{6})\s*\)/g;
      let pm;
      while ((pm = pairRe.exec(section)) !== null) {
        const name = cleanCandidateName_(pm[1]);
        const code = normalizeCandidateCode_(pm[2]);
        if (!name || !code || seenThisMessage.has(code)) continue;
        addCandidateMention_(byCode, seenThisMessage, code, name, Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd'));
        parsedCardCount += 1;
      }

      // Defensive support for one-line cards: 삼성전기 (009150)
      const sameRe = /(?:^|\n)\s*([^\n\r]{2,50}?)\s*[\[(]\s*(\d{6})\s*[\])]\s*(?:\n|$)/g;
      let sm;
      while ((sm = sameRe.exec(section)) !== null) {
        const name = cleanCandidateName_(sm[1]);
        const code = normalizeCandidateCode_(sm[2]);
        if (!name || !code || seenThisMessage.has(code)) continue;
        addCandidateMention_(byCode, seenThisMessage, code, name, Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd'));
        parsedCardCount += 1;
      }
    });
  });

  if (structuredMailCount > 0 && parsedCardCount === 0) {
    throw new Error('CANDIDATE_PARSE_EMPTY_V3_STRUCTURED_MAILS_' + structuredMailCount);
  }

  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - (YCI.TTL_DAYS - 1));
  const cutoffKey = Utilities.formatDate(cutoff, 'Asia/Seoul', 'yyyy-MM-dd');

  Object.keys(byCode).forEach(function(code) {
    if (byCode[code].lastSeen < cutoffKey) {
      delete byCode[code];
    } else {
      byCode[code].mentionDays = byCode[code].dates.size;
    }
  });

  return byCode;
}
