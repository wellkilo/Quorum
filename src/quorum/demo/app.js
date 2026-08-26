const button = document.querySelector('#replay-button');
const timeline = document.querySelector('#timeline');
const receiptList = document.querySelector('#receipt-list');
const runState = document.querySelector('#run-state');
const replayId = document.querySelector('#replay-id');
const comparison = document.querySelector('.comparison');
const messageField = document.querySelector('#message-field');

for (let index = 0; index < 72; index += 1) {
  const dot = document.createElement('i');
  dot.style.left = `${(index * 37) % 97}%`;
  dot.style.top = `${(index * 61) % 93}%`;
  dot.style.animationDelay = `${(index % 12) * 40}ms`;
  messageField.append(dot);
}

const delay = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function runReplay() {
  button.disabled = true;
  button.querySelector('span').textContent = 'Replaying…';
  runState.textContent = 'Running';
  runState.classList.add('live');
  comparison.classList.remove('replaying');
  void comparison.offsetWidth;
  comparison.classList.add('replaying');
  timeline.replaceChildren();
  receiptList.replaceChildren();

  try {
    const response = await fetch('/demo/replays/synthetic-week', { method: 'POST' });
    if (!response.ok) throw new Error('Replay API unavailable');
    const data = await response.json();
    replayId.textContent = data.replay_id;

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
        receipt.append(text, undo);
        receiptList.append(receipt);
      }
    }
    runState.textContent = 'Complete';
  } catch (error) {
    runState.textContent = 'Unavailable';
    const row = document.createElement('li');
    row.className = 'placeholder';
    row.textContent = 'The replay could not start. Run the local server and try again.';
    timeline.replaceChildren(row);
  } finally {
    runState.classList.remove('live');
    button.disabled = false;
    button.querySelector('span').textContent = 'Replay again';
  }
}

button.addEventListener('click', runReplay);
