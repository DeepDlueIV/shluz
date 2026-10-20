# Telegram recovery, optional wallet and guest journey

Owner authorizes implementation, publication to existing preview branch, NOT main or live provider/payment changes.
Base: 92c3b9e3, tree ff73f8a2f8f857e9314a09b95cb645713f30f08e.
The prior response stopped during an incomplete Git tree upload. This workspace starts from the verified full base; no owner data available here.

- [x] Reproduce archive/restore failure; distinguish archive pause from explicit revocation; admin recovery with Telegram ID; never revive revoked secrets or reset used quota.
- [x] Optional payg alongside unchanged subscription: per-channel assignment, shared USD wallet, presentation credits = USD × 100, explicit markup in request price, idempotent ledger and conservative pre-reservation. No payment gateway or surprise activation.
- [x] Document actual Venice price sources and limits; no assertion that a model table is an invoice.
- [x] Document landing → guest dashboard → single generation → OTP upgrades same account. Preserve demo usage; no automatic destructive cleanup of financial records.
- [ ] Focused tests, full suite, migration, CI, preview; final short instructions for owner.

Rulings: An invitation must not bypass an explicit block. Old archive data cannot reveal original subscription expiry; admin recovery assigns a selected plan when none is current. New archives only pause what was active.
Rulings: Credits are a transparent display conversion, not a second balance or hidden top-up fee. Price includes owner markup. Unknown provider cost keeps a reservation; it is not charged as zero. Initially allow only reviewed bounded models (mock by default).
Rulings: Frontend is parallel work; preserve web/. Define its journey in shared contract, do not invent that redirect is rendered by this gateway.

Fresh verification: full local suite and Alembic upgrade/check run in isolated SQLite.
Browser launch locally is blocked by the managed browser network policy before reaching HTTP;
leave production browser settings intact and use the existing Chromium CI job.
The financial precision regression failed on SQLite Numeric; wallet values now store integer
1e-8 USD via FixedUSD. Known non-USD provider charges retain an uncertain hold, never free USD.
No new live API calls or owner data access. CI/preview publication still to verify.
