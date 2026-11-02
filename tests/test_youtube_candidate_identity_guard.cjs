// Regression test for the actual source branch in Apps Script Runtime V8.
// Does not contact Gmail, Naver, or any live spreadsheet.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const source = fs.readFileSync(path.join(__dirname, '../apps_script/youtube_candidate_runtime_v8.gs'), 'utf8');
const start = source.indexOf('      if (!identity.verified) {');
const end = source.indexOf('      const rowNumber = prior ?', start);
assert.ok(start >= 0 && end > start, 'identity checks must precede candidate row mutation');
const actualGuards = source.slice(start, end);

// Execute the guard excerpt from the real implementation with controlled stubs.
// A rejected candidate returns undefined, and an allowed candidate returns true.
const check = new Function('ctx', `
  const { identity, prior, item, aliasMap, sheet, ss,
    appendIdentityIssueV8_, isSameStockNameV8_ } = ctx;
  const code = item.code;
  const started = Date.now();
  ${actualGuards}
  return true;
`);

function scenario({name, code='008060', verified=false, canonical='',
                   priorName=null, incoming='대덕전자'}) {
  const errors = [];
  const prior = priorName === null ? null : {row: 154, market: 'KOSPI'};
  const ctx = {
    identity: {verified, name: canonical, market: verified ? 'KOSPI' : ''},
    prior, item: {code, name: incoming}, aliasMap: {}, ss: {},
    sheet: {getRange: () => ({getDisplayValue: () => priorName})},
    appendIdentityIssueV8_: (_ss, error) => errors.push(error.error),
    isSameStockNameV8_: (_code, input, expected) => input === expected,
  };
  const result = check(ctx);
  assert.equal(Boolean(result), Boolean(name.allow), name.title);
  if (!name.allow) assert.ok(errors.length > 0, name.title + ' must be quarantined');
}

test('Unverified new company cannot be registered', () =>
  scenario({name: {title:'unverified new',allow:false}, priorName:null}));
test('Unverified wrong name cannot refresh an existing different company', () =>
  scenario({name: {title:'unverified mismatch',allow:false}, priorName:'대덕'}));
test('Unverified same name may refresh a previously established candidate', () =>
  scenario({name: {title:'existing same name',allow:true}, priorName:'대덕',incoming:'대덕'}));
test('Verified wrong company name is quarantined', () =>
  scenario({name: {title:'verified mismatch',allow:false}, verified:true,canonical:'대덕',priorName:'대덕'}));
test('Verified matching code and name can proceed', () =>
  scenario({name: {title:'verified match',allow:true}, verified:true,canonical:'대덕',priorName:null,incoming:'대덕'}));
