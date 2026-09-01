# Quorum Demo Recording Checklist

## Hard gates before recording

- [ ] Open the public demo in a signed-out browser: <https://wellkilo.github.io/Quorum/>.
- [ ] Confirm the repository is public and GitHub identifies the root `LICENSE` as Apache-2.0.
- [ ] Enter the actual AWS Builder ID in Devpost; never record the generic AWS profile URL as an ID.
- [ ] Decide whether 4:15–4:35 uses the honest no-quote card or a real quotation with written
      consent for its exact wording and public use.
- [ ] Keep every current replay number labeled `synthetic`; do not call it a pilot, study, or user
      result.
- [ ] Show both successful workflow runs and the short-lived/cleaned boundary; do not imply continuous
      hosting or expose an AWS console or account details.

## Capture setup

- [ ] Record at 1920x1080, 30 fps, with system scaling at 100 percent.
- [ ] Use a clean browser profile with bookmarks, extensions, notifications, and account avatars
      hidden.
- [ ] Set browser zoom so the synthetic status line and both metric panels fit at once.
- [ ] Close Slack, email, terminal tabs, password managers, and any window that can expose PII or
      credentials.
- [ ] Use the checked-in `assets/quorum-architecture.png` for the architecture scene.
- [ ] Prepare the repository evaluation section and verify the 50-case dataset count immediately
      before capture.
- [ ] Rehearse one complete replay click; reload before the recorded take so the state begins at
      `Ready`.

## Capture order

- [ ] Record the public replay page from cold open through the completed receipt trail.
- [ ] Record the architecture image with a slow left-to-right pan, then the separate state row.
- [ ] Record the Runtime run showing READY, HTTP 503, and cleanup, then the Memory/Gateway run showing
      ACTIVE/READY, zero events/tool calls, and cleanup.
- [ ] Record the README evaluation evidence without exposing local paths or terminal history.
- [ ] Record the repository root and visible Apache-2.0 license.
- [ ] Record the actual Builder ID field only after verifying it is safe and correct.
- [ ] Capture five seconds of clean room tone for audio repair.

## Edit and export

- [ ] Follow `docs/video/storyboard.md`; target 4:50 and reject any export over 5:00.
- [ ] Import `docs/video/quorum-demo.en.vtt`, then check every cue against the final narration.
- [ ] Keep captions within two lines and inside title-safe margins.
- [ ] Normalize voice level and remove notification sounds; do not use music that obscures speech.
- [ ] Export H.264 MP4 at 1920x1080 with AAC audio.
- [ ] Watch the exported file once at normal speed and once muted to verify visual comprehension.

## Publication and final evidence

- [ ] In the dedicated test workspace, record the live-evidence preview without showing environment
  variables, tokens, channel IDs, user IDs, or shell history.
- [ ] Start the confirmed live-evidence command, type only its fixed synthetic marker in Slack, and
  capture the terminal's PII-safe `provider_responses_validated` report.
- [ ] Show the one-line group receipt, the one private question, and the one-screen weekly summary;
  keep all workspace and participant names outside the crop.
- [ ] State that the clip proves Slack transport and synthetic surface delivery, not a real pilot or
  measured impact result.

- [ ] Upload to YouTube or Vimeo with public or unlisted access, never private access.
- [ ] Verify playback in a signed-out browser and confirm the platform duration is at most 5:00.
- [ ] Enable English captions and inspect the first 30 seconds manually.
- [ ] Put the final video URL in Devpost and README only after signed-out verification.
- [ ] Save a screenshot of the final Devpost fields and all public URLs before submission lock.
