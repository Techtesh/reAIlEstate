// Run k6 from another machine, against an environment you own.
import http from 'k6/http';
import { check, sleep } from 'k6';
const base = (__ENV.BASE_URL || '').replace(/\/$/, '');
if (!base.startsWith('https://')) throw new Error('Set BASE_URL to your HTTPS site');
const users = Number(__ENV.USERS || 1000);
if (!Number.isInteger(users) || users < 1 || users > 1000) {
  throw new Error('USERS must be an integer from 1 to 1000');
}
const paths = (__ENV.PATHS || '/').split(',');
if (paths.some(path => !path.startsWith('/') || path.startsWith('//'))) {
  throw new Error('PATHS must be comma-separated local URL paths');
}
export const options = {
  stages: [
    { duration: '2m', target: Math.min(users, 50) },
    { duration: '3m', target: Math.min(users, 250) },
    { duration: '3m', target: users },
    { duration: '5m', target: users },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    http_req_failed: [{ threshold: 'rate<0.01', abortOnFail: true, delayAbortEval: '1m' }],
    http_req_duration: ['p(95)<2000'],
    checks: ['rate>0.99'],
  },
};
export default function () {
  const path = paths[(__VU + __ITER) % paths.length];
  const response = http.get(base + path, { tags: { endpoint: path }, timeout: '15s' });
  check(response, { 'HTTP 200': r => r.status === 200 });
  sleep(5 + Math.random() * 10);
}
