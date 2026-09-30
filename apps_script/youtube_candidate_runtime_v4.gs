/*
 * Runtime V4: robust Gmail candidate parser.
 *
 * Why V4 exists:
 * GmailApp.getPlainBody() can differ from the Gmail API / rendered mail text.
 * We therefore parse the dedicated candidate section from BOTH plain text and
 * a lightweight HTML-to-text conversion.  Candidate identity is anchored on
 * an explicit parenthesized six-digit ticker, while the closest preceding
 * plausible line is retained as the source name for name/code mismatch checks.
 */

function candidateTextFromHtmlV4_(html) {
  return String(html || '')
    .replace(/<br\s*\/?\s*>/gi, '\n')
    .replace(/<\/(?:p|div|li|tr|h[1-6]|table)>/gi, '\n')
    .replace(/<[^>]+>/g, ' ')
    .replace(/&nbsp;|&#160;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&quot;/gi, '"')
    .replace(/&#40;/gi, '(')
    .replace(/&#41;/gi, ')');
}

function normalizeCandidateTextV4_(text) {
  return String(text || '')
    .replace(/\r\n/g, '\n')
    .replace(/\r/g, '\n')
    .replace(/[\u00a0\u200b\ufeff\u200e\u200f]/g, ' ')
    .replace(/[\u202a-\u202e\u2066-\u2069]/g, '')
    .replace(/[（]/g, '(')
    .replace(/[）]/g, ')')
    .replace(/[０-９]/g, function(ch) { return String(ch.charCodeAt(0) - 0xFF10); });
}

function candidateSectionV4_(text) {
  const body = normalizeCandidateTextV4_(text);
  let markerPos = body.lastIndexOf('오늘 언급·주목 종목');
  if (markerPos < 0) markerPos = body.lastIndexOf('언급·주목 종목');
  if (markerPos < 0) return '';
  return body.substring(markerPos);
}

function sourceNameBeforeCodeV4_(section, codeStart) {
  const prefix = section.substring(Math.max(0, codeStart - 500), codeStart);
  const lines = prefix.split('\n');
  for (let i = lines.length - 1; i >= 0; i--) {
    const line = String(lines[i] || '')
      .replace(/^\s*[-*+•▪◦‣▶▷►]+\s*/, '')
      .replace(/\*\*|__|`/g, '')
      .trim();
    if (!line) continue;
    if (plausibleCandidateName_(line)) return cleanCandidateName_(line);
  }
  return '';
}

function extractCandidateCardsV4_(section) {
  const out = [];
  const seen = new Set();

  // Primary: explicit parenthesized six-digit ticker anywhere in the dedicated section.
  const codeRe = /\(\s*(\d{6})\s*\)/g;
  let m;
  while ((m = codeRe.exec(section)) !== null) {
    const code = normalizeCandidateCode_(m[1]);
    if (!code || seen.has(code)) continue;
    const name = sourceNameBeforeCodeV4_(section, m.index);
    out.push({code: code, name: name});
    seen.add(code);
  }

  // Defensive fallback: a standalone six-digit line if parentheses were stripped.
  if (out.length === 0) {
    const lineRe = /(?:^|\n)\s*(\d{6})\s*(?:\n|$)/g;
    while ((m = lineRe.exec(section)) !== null) {
      const code = normalizeCandidateCode_(m[1]);
      if (!code || seen.has(code)) continue;
      const name = sourceNameBeforeCodeV4_(section, m.index);
      out.push({code: code, name: name});
      seen.add(code);
    }
  }

  return out;
}

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

      const plainSection = candidateSectionV4_(message.getPlainBody());
      const htmlSection = candidateSectionV4_(candidateTextFromHtmlV4_(message.getBody()));
      if (!plainSection && !htmlSection) return;
      structuredMailCount += 1;

      let cards = plainSection ? extractCandidateCardsV4_(plainSection) : [];
      if (cards.length === 0 && htmlSection) cards = extractCandidateCardsV4_(htmlSection);
      if (cards.length === 0) return;

      const dateKey = Utilities.formatDate(message.getDate(), 'Asia/Seoul', 'yyyy-MM-dd');
      cards.forEach(function(card) {
        const code = card.code;
        const sourceName = String(card.name || '').trim();

        if (!byCode[code]) {
          byCode[code] = {
            name: sourceName,
            nameTrusted: Boolean(sourceName),
            dates: new Set(),
            lastSeen: dateKey,
          };
        }
        byCode[code].dates.add(dateKey);
        if (dateKey >= byCode[code].lastSeen) {
          byCode[code].lastSeen = dateKey;
          if (sourceName) {
            byCode[code].name = sourceName;
            byCode[code].nameTrusted = true;
          }
        }
        parsedCardCount += 1;
      });
    });
  });

  if (structuredMailCount > 0 && parsedCardCount === 0) {
    throw new Error('CANDIDATE_PARSE_EMPTY_V4_STRUCTURED_MAILS_' + structuredMailCount);
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

/*
 * Override V2 refresh so a code-only fallback never creates a false mismatch.
 * If a trusted source name was extracted, the existing strict mismatch
 * quarantine remains in force.
 */
function refreshYouTubeCandidatePoolV2() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(30000)) return;
  const started = Date.now();
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(YC.CANDIDATE_SHEET);
    if (!sheet) throw new Error('CANDIDATES sheet not found');

    const pool = collectIntelligenceCandidates_();
    const existing = existingCandidateRowsV2_(sheet);
    const today = Utilities.formatDate(new Date(), 'Asia/Seoul', 'yyyy-MM-dd');
    const activeCodes = new Set();

    Object.keys(pool).sort().forEach(function(code) {
      const item = pool[code];
      const sourceName = String(item.name || '').trim();
      const identity = resolveNaverIdentity_(code, sourceName || code);

      if (item.nameTrusted && identity.verified && identity.name && normalizeName_(identity.name) !== normalizeName_(sourceName)) {
        appendDispatchLog_(ss, {
          ticker: code, name: sourceName, tech: 'IDENTITY_QUARANTINE', http: '',
          finalSignal: '', flowStatus: '', detail: 'Naver=' + identity.name,
          triggerKey: '', elapsedMs: Date.now() - started,
          error: 'SOURCE_NAME_CODE_MISMATCH', source: 'GMAIL_INGEST_V4',
        });
        return;
      }

      const prior = existing[code] || null;
      const rowNumber = prior ? prior.row : firstBlankCandidateRow_(sheet);
      const market = identity.market || (prior ? prior.market : '');
      if (!market) {
        appendDispatchLog_(ss, {
          ticker: code, name: sourceName || code, tech: 'IDENTITY_PENDING', http: '',
          finalSignal: '', flowStatus: '', detail: '', triggerKey: '',
          elapsedMs: Date.now() - started, error: 'MARKET_UNRESOLVED', source: 'GMAIL_INGEST_V4',
        });
        return;
      }

      const canonicalName = identity.name || sourceName || code;
      const ticker = (market === 'KOSDAQ' ? 'KOSDAQ:' : 'KRX:') + code;
      const lastSeen = parseYmd_(item.lastSeen);
      const expiry = new Date(lastSeen.getTime());
      expiry.setDate(expiry.getDate() + YCI.TTL_DAYS);

      sheet.getRange(rowNumber, 1, 1, 7).setValues([[
        'Y', canonicalName, market, code, ticker, lastSeen, expiry,
      ]]);
      sheet.getRange(rowNumber, 4).setNumberFormat('@');
      sheet.getRange(rowNumber, 6, 1, 2).setNumberFormat('yyyy-mm-dd');
      activeCodes.add(code);

      const note = String(sheet.getRange(rowNumber, 26).getDisplayValue() || '');
      if (note.indexOf('TECH_ASOF=' + today) < 0) {
        try {
          const tech = fetchDailyTechnicalSnapshot_(code);
          if (tech.staleDays > 4) throw new Error('STALE_DAILY_BAR_' + tech.staleDays);
          sheet.getRange(rowNumber, 9, 1, 7).setValues([[
            tech.referenceLow, tech.atr14, tech.ma20, tech.ma60,
            tech.ma20Slope5d, tech.rsi14, tech.pullbackReady,
          ]]);
          sheet.getRange(rowNumber, 26).setValue(
            'TECH_ASOF=' + today + '; LAST_BAR=' + tech.lastDate + '; MENTION_DAYS=' + item.mentionDays
          );
        } catch (err) {
          sheet.getRange(rowNumber, 26).setValue('TECH_REFRESH_ERROR=' + String(err && err.message ? err.message : err));
        }
      }
    });

    Object.keys(existing).forEach(function(code) {
      if (!activeCodes.has(code)) sheet.getRange(existing[code].row, 1).setValue('N');
    });

    SpreadsheetApp.flush();
    PropertiesService.getScriptProperties().setProperty('LAST_CANDIDATE_REFRESH_AT', new Date().toISOString());
  } finally {
    lock.releaseLock();
  }
}
