const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(__dirname + '/load-test.js', 'utf8')
  .replace(/^import .*;$/gm, '')
  .replace('export const options', 'const options')
  .replace('export default function ()', 'function journey()');
function scenario(env) {
  const requests = [];
  const pauses = [];
  const context = {
    __ENV: env, __VU: 1, __ITER: 0,
    http: { get: (url) => { requests.push(url); return {status: 200}; } },
    check: (response, checks) => { for (const check of Object.values(checks)) assert.ok(check(response)); },
    sleep: seconds => pauses.push(seconds),
  };
  vm.runInNewContext(source + '; journey(); result = options;', context);
  return { ...context, requests, pauses };
}
const valid = scenario({ BASE_URL: 'https://test.example/', USERS: '50', PATHS: '/,/projects' });
assert.equal(valid.requests[0], 'https://test.example/projects');
assert.equal(valid.result.stages[2].target, 50);
assert.ok(valid.pauses[0] >= 5 && valid.pauses[0] <= 15);
assert.equal(scenario({BASE_URL: 'https://test.example'}).result.stages[2].target, 1000);
for (const env of [
  {}, {BASE_URL: 'http://test.example'},
  {BASE_URL: 'https://test.example', USERS: '0'},
  {BASE_URL: 'https://test.example', USERS: '1001'},
  {BASE_URL: 'https://test.example', USERS: '1.5'},
  {BASE_URL: 'https://test.example', PATHS: '//other.example'},
]) assert.throws(() => scenario(env));
console.log('Load scenario checks passed (mocked; no network traffic).');
