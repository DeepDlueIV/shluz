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
