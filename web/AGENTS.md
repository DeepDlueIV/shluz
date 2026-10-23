# NoClip web delivery

- Work in `web/`; leave gateway and Telegram behavior unchanged unless explicitly requested.
- EN is the initial language; keep RU translations. Preserve drafts during preview, language and toolbar changes.
- The owner reviews `https://noclip-web.vercel.app`. Reuse Vercel project `prj_IMSWjsl9RU0e8mV1RXv7QyLz0VJD`; do not create another project or mirror repository.
- With this connection, first call project/deployment tools with the known project/deployment ID and OMIT optional `teamId` and `slug`. This was verified to work. Empty `list_teams` is not proof of missing project access. A 403 on a particular endpoint must not be generalized to the whole connection; do not ask for blind reconnects.
- Auto preview deployments are disabled at project level. Release reviewed web changes through `main` after checks. Other branches are tested in GitHub Actions, not repeatedly deployed to Vercel. Do not publish Telegram/backend branches as Next.js.
- `vercel.json` skips a build when `web/` did not change in the latest commit. Exit 0 means skip; exit 1 means build. Keep the project Root Directory `web`.
- Use the existing Git integration; do not trigger duplicate deploys for the same commit. Confirm the SHA and READY status before saying a version is live.
- Keep Hobby, deployment protection and `NEXT_PUBLIC_GATEWAY_ENABLED=false` unchanged. No provider secrets belong in this client.
- Preserve failed deployment history for diagnosis. Do not delete unrelated deployments, alter other projects, or turn on paid features.

## Efficient verification

- Follow `docs/ci-workflow.md`: use Draft PRs while iterating and targeted local tests for the changed behavior. Draft CI runs units/types only, not the whole browser suite.
- Mark the finished PR Ready once to run the complete build and browser suite. Merge only after that full run succeeds for the current revision. If debugging resumes, return the PR to Draft; never treat a quick green check as full verification.
- Working branch pushes do not need a second CI run alongside the PR. After merge, main runs quick checks; do not manually duplicate a completed full run or Vercel deployment.
- Request source artifacts only when actually needed. Read the existing results/logs once; avoid repeated downloads and tight status polling. Keep the owner informed at milestones.

## Interaction and language regressions

- Implement every UI/text change in both EN and RU, even when the owner supplies only English wording, unless explicitly instructed otherwise. Keep equivalent behavior, accessible labels, errors and layout in both languages.
- For interruptible hover disclosures, keep stable DOM nodes and one animation owner per property. Continue from current visual values; do not remount/replay the entrance on repeated hover or enqueue stale transitions. Keep the existing persistent prompt-preview logic.
- Do not fade an ancestor of glass buttons: ancestor opacity below 1 creates a backdrop root and can make blur snap at the end. Fade the glass element itself, retaining its backdrop-filter; keep its wrappers opaque and do not add will-change:opacity to them.
- Hover-open followed by a pointer click must not immediately toggle the disclosure closed. Preserve the shared hover region, cancel pending close on re-entry, and keep keyboard/touch controls and hidden-panel inert behavior.
- Before releasing new/changed animations, test intermediate frames, stationary hover, rapid exit/re-entry, fully closed/reopened state, touch and both languages. Keep blur/color/typography unchanged unless requested; do not fix flicker by deleting blur or animations.
