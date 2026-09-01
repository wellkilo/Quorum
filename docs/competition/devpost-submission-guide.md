# Devpost Registration and Submission Guide

Official challenge: <https://agentsforhumans.devpost.com/>

## Registration

1. Sign in to the Devpost account that will own the submission.
2. Select **Join hackathon** on the official challenge page.
3. Confirm that the dashboard lists Agents for Humans under registered hackathons.
4. Keep the representative identity consistent with the AWS Builder ID used at submission.
5. Do not add an organization as the entrant unless it is intentionally participating and has passed the eligibility review.

## Draft submission

Create the draft early and keep these fields aligned with the public repository:

- Project name: `Quorum`
- Track: `Good Neighbor Agents`
- One-line pitch: `A group coordination agent whose primary success metric is how rarely it interrupts people.`
- Repository: <https://github.com/wellkilo/Quorum>
- License: Apache-2.0
- Built with: Strands Agents SDK and Amazon Bedrock AgentCore
- Live demo: <https://wellkilo.github.io/Quorum/>
- Video: leave blank until a public YouTube or Vimeo upload is verified
- AWS Builder ID: enter the account's actual Builder ID; the generic profile URL is not a public identifier

## Evidence checklist

- [x] Repository is public.
- [x] GitHub recognizes the root `LICENSE` as Apache-2.0.
- [ ] README setup commands work from a clean environment.
- [x] Architecture diagram matches the verified short-lived AgentCore paths and cleanup boundary.
- [x] Short-lived AgentCore Runtime deployment evidence is public and automatic cleanup is verified.
- [x] Short-lived AgentCore Memory and Gateway evidence is public, zero-call, and cleaned up.
- [ ] Video is public, five minutes or shorter, and demonstrates the working project.
- [x] Public demo works without an invitation, private account, or payment.
- [ ] Demo remains online through the end of judging.
- [ ] All real-world metrics have provenance and consent.
- [x] All synthetic metrics are visibly labeled synthetic.
- [ ] Three Builder Center URLs are included and each title contains `Agents for Humans`.
- [ ] Pre-existing code and AI assistance are disclosed.

## Final submission lock

Before pressing submit, export or screenshot every field and save the public URLs. After the submission deadline, Devpost may not permit substantive edits. Test the repository, video, and demo links in a signed-out browser session.
