# Builder Center Publication Pack

These three English drafts form one chronological build narrative for the AWS Agents for Humans
Hackathon. Each title contains the required phrase `Agents for Humans`. Publishing remains a manual
external action because the entrant must sign in, review the final preview, and own the post.

| Order | Draft | Suggested subtitle | Primary evidence |
| --- | --- | --- | --- |
| 1 | [Building an Agent That Tries Not to Appear](01-agents-for-humans-building-an-agent-that-tries-not-to-appear.md) | Why Quorum treats fewer interruptions as the product | Public replay and four mechanisms |
| 2 | [Putting Agent Autonomy Behind a Native Interrupt](02-agents-for-humans-putting-autonomy-behind-a-native-interrupt.md) | A deterministic Strands Graph with a fail-closed tool boundary | Architecture diagram and policy tests |
| 3 | [Testing an Agent That Must Not Invent Commitments](03-agents-for-humans-testing-an-agent-that-must-not-invent-commitments.md) | Source-grounded extraction, negative tests, and an honest baseline | 50-case gold set and executable regression suite |

Suggested tags: `agents-for-humans`, `amazon-bedrock-agentcore`, `strands-agents`, `responsible-ai`,
`testing`. Use only tags supported by the Builder Center editor.

Suggested cover image for posts 2 and 3: `assets/quorum-architecture.png`. Alt text:

> Quorum architecture showing a five-node Strands Graph, native hook interrupt, reversible tools,
> and separate PostgreSQL, session, AgentCore Memory, Runtime, and observability boundaries.

## Publication checklist for every post

- [ ] Confirm the title still contains the exact phrase `Agents for Humans`.
- [ ] Paste as rich text and inspect headings, lists, code blocks, and links in preview.
- [ ] Keep the GitHub Pages replay labeled synthetic and do not imply that it is AgentCore-hosted.
- [ ] Keep the no-pilot, no-user-quote, no-Bedrock-score, no-Gateway-tool-execution, and
      no-production-trace limitations that apply to that post; describe the managed span only as one
      synthetic zero-call probe, and every AgentCore lifecycle as short-lived, verified, and cleaned.
- [ ] Verify the repository and demo links in a signed-out browser.
- [ ] Publish under the entrant's actual Builder ID profile.
- [ ] Copy the final public URL into the table below and into Devpost.
- [ ] Save a screenshot of the published title, author, date, and URL.

## Published URLs

| Post | Public URL | Verified signed out |
| --- | --- | --- |
| 1 | Pending publication | No |
| 2 | Pending publication | No |
| 3 | Pending publication | No |

Do not mark the repository's three-post checklist complete until all three public URLs work without
the author's authenticated session.
