const button = document.querySelector('#replay-button');
const timeline = document.querySelector('#timeline');
const receiptList = document.querySelector('#receipt-list');
const runState = document.querySelector('#run-state');
const replayId = document.querySelector('#replay-id');
const comparison = document.querySelector('.comparison');
const messageField = document.querySelector('#message-field');
const evidenceSource = document.querySelector('#evidence-source');
const evidenceMode = document.querySelector('#evidence-mode');
const staticEvidenceHost = window.location.hostname === 'wellkilo.github.io'
  || new URLSearchParams(window.location.search).get('mode') === 'static';

if (!staticEvidenceHost) {
  evidenceMode.lastElementChild.textContent = 'Runtime replay · synthetic data only';
  evidenceSource.textContent = 'Synthetic Runtime replay. This is not a measured real-world outcome.';
}

for (let index = 0; index < 72; index += 1) {
  const dot = document.createElement('i');
  dot.style.left = `${(index * 37) % 97}%`;
  dot.style.top = `${(index * 61) % 93}%`;
  dot.style.animationDelay = `${(index % 12) * 40}ms`;
  messageField.append(dot);
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function validateReplayData(data) {
  const valid = data
    && data.dataset_id === 'synthetic_week_v1'
    && data.data_classification === 'synthetic'
    && typeof data.replay_id === 'string'
    && typeof data.baseline?.message_count === 'number'
    && typeof data.baseline?.closed_decisions === 'number'
    && typeof data.baseline?.decision_latency_p50_hours === 'number'
    && typeof data.quorum?.interruption_count === 'number'
    && typeof data.quorum?.closed_decisions === 'number'
    && typeof data.quorum?.decision_latency_p50_hours === 'number'
    && Array.isArray(data.timeline)
    && Array.isArray(data.receipts)
    && typeof data.disclaimer === 'string';
  if (!valid) throw new Error('Replay data failed its public evidence contract');
  return data;
}

async function loadReplayData() {
  if (staticEvidenceHost) {
    const response = await fetch('./synthetic-week.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('Static evidence replay unavailable');
    return { data: validateReplayData(await response.json()), source: 'static' };
  }

  const response = await fetch('./demo/replays/synthetic-week', { method: 'POST' });
  if (!response.ok) throw new Error('Runtime replay API unavailable');
  return { data: validateReplayData(await response.json()), source: 'runtime' };
}

async function runReplay() {
  button.disabled = true;
  button.setAttribute('aria-busy', 'true');
  button.querySelector('span').textContent = 'Replaying the week…';
  runState.textContent = 'Running';
  runState.classList.add('live');
  comparison.classList.remove('replaying');
  void comparison.offsetWidth;
  comparison.classList.add('replaying');
  timeline.replaceChildren();
  receiptList.replaceChildren();
  document.querySelector('.comparison').scrollIntoView({
    behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
    block: 'center',
  });

  try {
    const { data, source } = await loadReplayData();
    replayId.textContent = data.replay_id;
    evidenceSource.textContent = source === 'static'
      ? 'Versioned static evidence replay on GitHub Pages. AgentCore deployment is not claimed.'
      : data.disclaimer;

    document.querySelector('#baseline-messages').textContent = data.baseline.message_count;
    document.querySelector('#baseline-decisions').textContent = data.baseline.closed_decisions;
    document.querySelector('#baseline-latency').textContent = `${(data.baseline.decision_latency_p50_hours / 24).toFixed(1)}d`;
    document.querySelector('#quorum-interruptions').textContent = data.quorum.interruption_count;
    document.querySelector('#quorum-decisions').textContent = data.quorum.closed_decisions;
    document.querySelector('#quorum-latency').textContent = `${data.quorum.decision_latency_p50_hours}h`;

    for (const [index, item] of data.timeline.entries()) {
      await delay(index === 0 ? 120 : 650);
      const row = document.createElement('li');
      row.textContent = item;
      timeline.append(row);
      if (index < data.receipts.length) {
        const receipt = document.createElement('div');
        receipt.className = 'receipt';
        receipt.style.animationDelay = `${index * 80}ms`;
        const text = document.createElement('span');
        text.textContent = data.receipts[index];
        const undo = document.createElement('button');
        undo.type = 'button';
        undo.disabled = true;
        undo.textContent = 'Undo';
        undo.title = source === 'static' ? 'Disabled in the static replay' : 'Demo receipt';
        receipt.append(text, undo);
        receiptList.append(receipt);
      }
    }
    runState.textContent = source === 'static' ? 'Complete · static' : 'Complete · runtime';
  } catch (error) {
    runState.textContent = 'Unavailable';
    const row = document.createElement('li');
    row.className = 'placeholder';
    row.textContent = staticEvidenceHost
      ? 'The static replay data is unavailable. Refresh the page and try again.'
      : 'The Runtime replay API is unavailable. Start the local server and try again.';
    timeline.replaceChildren(row);
  } finally {
    runState.classList.remove('live');
    button.disabled = false;
    button.removeAttribute('aria-busy');
    button.querySelector('span').textContent = 'Replay again';
  }
}

button.addEventListener('click', runReplay);
