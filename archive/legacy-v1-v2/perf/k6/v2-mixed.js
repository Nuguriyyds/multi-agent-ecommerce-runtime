import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

export const options = {
  vus: Number(__ENV.VUS || 5),
  duration: __ENV.DURATION || '30s',
  summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(90)', 'p(95)', 'p(99)'],
  thresholds: {
    http_req_failed: ['rate<0.2'],
  },
};

const baseUrl = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const thinkTime = Number(__ENV.SLEEP_SECONDS || 0);
const replyReadyMetric = new Rate('v2_reply_ready');
const needsClarificationMetric = new Rate('v2_needs_clarification');
const fallbackUsedMetric = new Rate('v2_fallback_used');
const refreshTriggeredMetric = new Rate('v2_refresh_triggered');
const messageLatencyMetric = new Trend('v2_message_reported_latency_ms');

function jsonHeaders(extra = {}) {
  return {
    headers: {
      'Content-Type': 'application/json',
      ...extra,
    },
  };
}

function recordMessageMetrics(response) {
  try {
    const payload = response.json();
    const details = payload.agent_details || {};
    const terminalState = details.terminal_state;
    replyReadyMetric.add(terminalState === 'reply_ready');
    needsClarificationMetric.add(terminalState === 'needs_clarification');
    fallbackUsedMetric.add(terminalState === 'fallback_used');
    refreshTriggeredMetric.add(Boolean(payload.recommendation_refresh_triggered));
    if (typeof details.latency_ms === 'number') {
      messageLatencyMetric.add(details.latency_ms);
    }
  } catch (_) {
    // Keep the load script resilient when an error response is returned.
  }
}

export default function () {
  const userId = `u_perf_v2_${__VU}_${__ITER}`;

  const createResponse = http.post(
    `${baseUrl}/api/v2/sessions`,
    JSON.stringify({ user_id: userId }),
    {
      ...jsonHeaders(),
      tags: { endpoint: 'v2_create_session' },
    },
  );
  check(createResponse, {
    'create session status is 200': (res) => res.status === 200,
  });
  if (createResponse.status !== 200) {
    return;
  }

  const sessionId = createResponse.json('session_id');
  const firstMessage = http.post(
    `${baseUrl}/api/v2/sessions/${sessionId}/messages`,
    JSON.stringify({
      message: '预算 3000',
      scene: 'default',
    }),
    {
      ...jsonHeaders(),
      tags: { endpoint: 'v2_message_turn_1' },
    },
  );
  check(firstMessage, {
    'first message status is 200': (res) => res.status === 200,
  });
  if (firstMessage.status === 200) {
    recordMessageMetrics(firstMessage);
  }

  const secondMessage = http.post(
    `${baseUrl}/api/v2/sessions/${sessionId}/messages`,
    JSON.stringify({
      message: '想买手机，最好适合游戏',
      scene: 'default',
    }),
    {
      ...jsonHeaders(),
      tags: { endpoint: 'v2_message_turn_2' },
    },
  );
  check(secondMessage, {
    'second message status is 200': (res) => res.status === 200,
  });
  if (secondMessage.status === 200) {
    recordMessageMetrics(secondMessage);
  }

  const homepageRead = http.get(
    `${baseUrl}/api/v2/users/${userId}/recommendations?scene=homepage`,
    {
      tags: { endpoint: 'v2_recommendations' },
    },
  );
  check(homepageRead, {
    'homepage read status is 200': (res) => res.status === 200,
  });

  let feedbackProductId = 'sku-redmi-k80';
  if (homepageRead.status === 200) {
    const firstProductId = homepageRead.json('products.0.product_id');
    if (firstProductId) {
      feedbackProductId = firstProductId;
    }
  }

  const feedbackResponse = http.post(
    `${baseUrl}/api/v2/users/${userId}/feedback-events`,
    JSON.stringify({
      event_type: 'click',
      scene: 'homepage',
      product_id: feedbackProductId,
      metadata: { source: 'k6', iteration: __ITER },
    }),
    {
      ...jsonHeaders(),
      tags: { endpoint: 'v2_feedback' },
    },
  );
  check(feedbackResponse, {
    'feedback status is 200': (res) => res.status === 200,
  });

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
