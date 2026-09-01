# Slack Test Workspace Setup

This runbook connects Quorum to a dedicated Slack test workspace without exposing a public webhook
or opening the Bedrock cost gate. The repository does not contain Slack tokens, signing secrets,
workspace IDs, channel IDs, message text, or participant data.

## 1. Create the app from the reviewed manifest

1. Open <https://api.slack.com/apps> and choose **Create New App → From an app manifest**.
2. Select the dedicated test workspace.
3. Paste [`src/quorum/slack-app-manifest.json`](../../src/quorum/slack-app-manifest.json).
4. Review the requested bot scopes before creating the app:
   - `channels:history` — receive messages only in public channels the bot can access;
   - `chat:write` — post the group receipt and weekly summary;
   - `im:write` — open the single private-question conversation.
5. Reject the installation if Slack displays any additional bot scope.

The manifest subscribes only to `message.channels`. It does not request private-channel history,
direct-message history, member profiles, files, reactions, or workspace administration.

## 2. Create short-lived local credentials

Under **Basic Information → App-Level Tokens**, create an app token with only
`connections:write`. Under **OAuth & Permissions**, install the app and copy the bot token. Never
paste either token into this repository, an issue, a screenshot, or a shell-history command. Inject
them through your local secret manager or an untracked `.env` file:

```bash
export QUORUM_SLACK_APP_TOKEN='<xapp token>'
export QUORUM_SLACK_BOT_TOKEN='<xoxb token>'
export QUORUM_SLACK_PSEUDONYM_KEY='<at least 16 random bytes>'
```

Invite Quorum to one dedicated public test channel. Do not add it to a real organization channel for
this verification.

## 3. Validate the configuration with zero network calls

```bash
uv run quorum-slack-socket validate
```

Expected output includes `transport=socket_mode`, the three reviewed scopes, one
`message.channels` subscription, `model_calls=0`, and `external_side_effect_calls=0`.

## 4. Prove one real Slack transport event without a model

Keep `QUORUM_BEDROCK_ENABLED=false` and `QUORUM_EXECUTION_ENABLED=false`, then run:

```bash
uv run quorum-slack-socket probe --timeout-seconds 120
```

Post one explicitly synthetic sentence in the dedicated test channel. The bridge acknowledges the
Socket Mode envelope before processing, pseudonymizes workspace/channel/user/message identifiers,
redacts mentions, email addresses, phone numbers, and IPv4 addresses, and emits only an opaque report.
The probe does not construct a model, Memory client, database engine, Gateway client, or Google
client and makes no outbound Slack Web API call. A successful report has all five side-effect
counters at zero.

This proves transport and redaction, not a model-backed ledger result. Do not publish the synthetic
test sentence as a real participant quotation.

## 5. Run the combined evidence pass

Preview the exact marker and side-effect count first:

```bash
uv run quorum-slack-live-evidence
```

Keep both cost gates false and start the confirmed command:

```bash
export QUORUM_BEDROCK_ENABLED=false
export QUORUM_EXECUTION_ENABLED=false
uv run quorum-slack-live-evidence \
  --confirm-live-posts \
  --timeout-seconds 120 \
  --output reports/slack-live-evidence.json
```

Then post the exact marker printed by preview in `QUORUM_SLACK_DEMO_CHANNEL_ID`. A marker from any
other channel is acknowledged but cannot trigger outbound posts. After the matching event, the
command disconnects Socket Mode and sends the three synthetic product surfaces. It refuses to
overwrite an existing report. The report excludes Slack IDs, message text, tokens, envelope IDs, and
provider timestamps. Inspect it before publishing and visually verify all three Slack messages.
If Slack rejects a call after an earlier message was accepted, inspect the channel and DM before
retrying; the command never claims atomic delivery and does not automatically retry external writes.

## 6. Post the three synthetic product surfaces separately

After inspecting the preview, provide the dedicated test channel and your own test-user ID:

```bash
uv run quorum-slack-smoke
export QUORUM_SLACK_DEMO_CHANNEL_ID='<test channel ID>'
export QUORUM_SLACK_DEMO_PARTICIPANT_ID='<your test user ID>'
uv run quorum-slack-smoke --confirm-live-posts
```

The confirmation flag performs exactly three visible Slack writes: one synthetic group receipt, one
synthetic private question, and one synthetic weekly summary. It makes zero Bedrock calls and zero
Gateway or Google Workspace tool calls. Use only the dedicated test workspace.

## 7. Optional HTTP adapter check

The ASGI application also implements Slack's signed HTTP Events API contract for deployments that
provide a public HTTPS webhook. The currently verified AgentCore Runtime invocation endpoint requires
IAM or OAuth authentication, so it is not claimed as a direct Slack Request URL. Test the adapter
locally without network credentials:

```bash
export QUORUM_SLACK_SIGNING_SECRET='local-slack-signing-secret'
export QUORUM_SLACK_PSEUDONYM_KEY='local-slack-pseudonym-key'
export QUORUM_BEDROCK_ENABLED=false
export QUORUM_EXECUTION_ENABLED=false
uv run python -c 'from quorum.runtime import app; app.run(port=18080)'

# In another shell:
export QUORUM_SLACK_SIGNING_SECRET='local-slack-signing-secret'
uv run quorum-slack-ingress-smoke --base-url http://127.0.0.1:18080
```

The smoke requires URL verification `200`, forged-signature rejection `401`, and a valid message
request stopped by the model cost gate with `503`. It reports zero model, Memory, database, Gateway,
and external side-effect calls.

## Evidence boundary

Do not claim a live Slack result until the `probe` command has received a real test-workspace event.
For the combined path, retain the generated report and a PII-safe screenshot showing the three
surfaces. Do not claim a live three-surface delivery until `--confirm-live-posts` has succeeded and
the messages have been visually inspected. Neither result is a real-world impact study.
