# Agents for Humans Rules Verification

Verified against the public Devpost overview, official rules, competition resources, current Strands documentation, and current Amazon Bedrock AgentCore documentation.

## Confirmed competition facts

- Submission period: August 10, 2026 at 09:00 PT through September 14, 2026 at 17:00 PT.
- Total cash pool: USD 40,000.
- Grand Prize: USD 10,000.
- Each of the three tracks awards USD 5,000 / 3,000 / 2,000.
- A project may win only one prize.
- Stage Two uses five equally weighted criteria: Technical Implementation, Design, Potential Impact, Creativity and Originality, and Presentation.
- Technical Implementation is the first tie-break criterion.
- A Builder Center post is worth 0.2 bonus points, capped at 0.6.
- The final maximum is 5.6.
- The post title should contain `Agents for Humans`. The rules note that the earlier `#AgentsforHumans` requirement was removed.
- The repository must be public and contain a visible MIT or Apache license file.
- The demo video must be public on YouTube or Vimeo and no longer than five minutes.
- A live demo and AgentCore deployment are optional but explicitly strengthen Technical Implementation.
- Registered participants may request USD 50 in promotional credits while supplies last. The request deadline is September 11, 2026 at 12:00 PT.
- Mainland China is not listed among excluded territories; Hong Kong is listed.
- The testing surface must remain available without charge or restriction through the judging period, which ends October 8, 2026.

## Confirmed SDK capability

- Strands provides Graph, conditional edges, execution limits, and multi-agent session management.
- Strands provides Swarm for dynamic multi-agent collaboration.
- Hooks can call `event.interrupt(...)` before a node or tool and resume with `interruptResponse`.
- AgentCore Runtime supports `BedrockAgentCoreApp` entrypoints and runtime session identifiers.
- AgentCore Memory supports short-term events and strategy-backed long-term memory, including Strands integration.
- AgentCore Gateway can expose MCP tools backed by Lambda or API targets.
- AgentCore Observability supports AWS Distro for OpenTelemetry and session-correlated traces.

## Project-specific cautions

- Do not describe a runtime session ID as durable graph persistence.
- Do not claim real-world impact while only synthetic data exists.
- Keep the license detectable by the repository host, not merely present as arbitrary text.
- Preserve an explicit disclosure of AI assistance and any future pre-existing material.
