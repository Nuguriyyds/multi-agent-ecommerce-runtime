import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

export const options = {
  vus: Number(__ENV.VUS || 10),
  duration: __ENV.DURATION || '30s',
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  thresholds: {
    http_req_failed: ['rate<0.2'],
  },
};

const baseUrl = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const numItems = Number(__ENV.NUM_ITEMS || 3);
const thinkTime = Number(__ENV.SLEEP_SECONDS || 0);
const stageNames = [
  'user_profile',
  'product_rec_coarse',
  'product_rec_ranked',
  'inventory',
  'marketing_copy',
];
const stageLatencyMetrics = Object.fromEntries(
  stageNames.map((stage) => [stage, new Trend(`v1_agent_latency_${stage}_ms`)]),
);
const stageDegradedMetrics = Object.fromEntries(
  stageNames.map((stage) => [stage, new Rate(`v1_agent_degraded_${stage}`)]),
);

function recordStageMetrics(response) {
  try {
    const payload = response.json();
    const details = payload.agent_details || {};
    for (const stage of stageNames) {
      const detail = details[stage];
      if (!detail) {
        continue;
      }
      if (typeof detail.latency_ms === 'number') {
        stageLatencyMetrics[stage].add(detail.latency_ms);
      }
      if (typeof detail.degraded === 'boolean') {
        stageDegradedMetrics[stage].add(detail.degraded);
      }
    }
  } catch (_) {
    // Keep the load script resilient when an error response is returned.
  }
}

export default function () {
  const payload = JSON.stringify({
    user_id: `u_perf_v1_${__VU}_${__ITER}`,
    num_items: numItems,
  });

  const response = http.post(`${baseUrl}/api/v1/recommend`, payload, {
    headers: { 'Content-Type': 'application/json' },
    tags: { endpoint: 'v1_recommend' },
  });

  check(response, {
    'v1 recommend status is 200': (res) => res.status === 200,
  });
  if (response.status === 200) {
    recordStageMetrics(response);
  }

  if (thinkTime > 0) {
    sleep(thinkTime);
  }
}

export function handleSummary(data) {
  if (__ENV.SUMMARY_PATH) {
    return {
      [__ENV.SUMMARY_PATH]: JSON.stringify(data, null, 2),
    };
  }
  return {};
}
