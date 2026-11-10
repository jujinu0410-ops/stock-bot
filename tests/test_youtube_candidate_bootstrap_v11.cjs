const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const cp = require('node:child_process');

const root = path.resolve(__dirname, '..');
const codePath = path.join(root,'apps_script/youtube_candidate_app/Code.js');
const code = fs.readFileSync(codePath,'utf8');
const sha = (code.match(/stock-bot\/([0-9a-f]{40})\/apps_script\//) || [])[1];
assert.ok(sha,'candidate bootstrap must retain an immutable base runtime SHA');

const listBlock = (code.match(/FILES:\s*\[([\s\S]*?)\]/) || [])[1];
assert.ok(listBlock,'bootstrap runtime file list missing');
const files = Array.from(listBlock.matchAll(/'([^']+\.gs)'/g)).map(m=>m[1]);
assert.ok(files.length >= 10,'expected full pinned V12 runtime bundle');

const embedded = (code.match(/const TWO_MAIL_V12_EMBEDDED = \x60([\s\S]*?)\x60;/) || [])[1];
assert.ok(embedded,'embedded two-mail collector missing');
const chunks = files.map(name => cp.execFileSync('git',['show',sha + ':apps_script/' + name],{
  cwd:root,encoding:'utf8',stdio:['ignore','pipe','pipe']
}));
const lifecycle = fs.readFileSync(path.join(root,'apps_script/youtube_candidate_lifecycle_v11.gs'),'utf8');
const stubs = [
  'scheduledCandidateMonitorV5 = function(){return "DELEGATED";};',
  'manualCandidateMonitorV5 = function(){return "DELEGATED";};',
  'monitorYouTubeCandidatesV2 = function(){return "DELEGATED";};',
  'monitorYouTubeCandidates = function(){return "DELEGATED";};'
].join('\n');
const source = chunks.join('\n\n') + '\n\n' + lifecycle + '\n\n' +
  embedded + '\n\n' + stubs + '\n\nscheduledCandidateLifecycleAndTickV11();';
new vm.Script(source,{filename:'candidate-runtime-v12-plus-lifecycle-v11.js'});

assert.match(code,/CacheService\.getScriptCache\(\)/,
  'lifecycle runtime must be cached rather than fetched on every five-minute tick');
assert.match(code,/YCV11_LIFECYCLE_MIGRATION_DONE/,
  'one-time migration must run before legacy refresh');
assert.match(code,/ycv11RegisterFreshMentionsV11_\(fresh\);/,
  'new candidate mails must create durable evaluation events before acknowledgement');
assert.doesNotMatch(code,/catch\s*\(historyError\)/,
  'history-persistence errors must not be swallowed before marking mail processed');
assert.match(code,/scheduledCandidateLifecycleAndTickV11/,
  'scheduled monitor must execute the lifecycle wrapper');

console.log('PASS: dynamic candidate runtime syntax, immutable base SHA, and cache/migration wiring.');
