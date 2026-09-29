# Operational stock outbox — implementation draft

**Not a deployment candidate.** Based on the readable release commit
`bdd247fbe666a15ee078cf2685f76a7f009ad030`. Production was separately reported at
`a62ea3dd3a31126dd394397228f5c0ee543da869`; that object is not available from
GitHub, so the latest production tree has not been integrated or verified.

## Behavior implemented

Operational validation remains in the existing endpoints. Stock bookkeeping is
saved as an immutable obligation in the **same document write** as newly accepted
operator progress, packing, loading, delivery confirmation or sales delivery.
No stock reservation is taken before that operational write. Failed source writes
cannot leave stock work behind. Existing stock enablement/recognition settings
continue to select which stage creates an obligation.

A background worker uses the durable stock engine with stable event IDs. Stock
shortages, missing opening, foreign reservations and unknown conversion remain
pending; the worker never fabricates balance or releases another operation's
claim. Applied balances with missing movements resume without a second debit.
Opening recorded after the source event requires stock review, avoiding historical
backfill. Seeded legacy progress is excluded. Disabling print stock recognition
does not accumulate work for later automatic backfill.

PO details expose a pending notice; Stock Saat Ini has a read-only pending panel.
Explicit retry uses existing `current_stock.update`, without expanding operational
permissions. Reading the panel never processes stock. The API currently shows at
most 100 pending tasks; this is not a global reconciliation-complete count.

Permitted SJ deletion archives its obligation for stock review before deleting the
source. It does not automatically reverse stock. A worker already applying stock
may finish concurrently; the archived obligation remains review-required rather
than falsely complete. No claim is made of atomic cancellation across documents.

## Local evidence

MongoDB 8.0.12, loopback only, isolated `eternal_confirm_sj_stock_test`.

- 26 new tests cover real endpoints, lack of stock/opening, existing foreign claim,
  operational qty/sample/SJ/photo/permission guards, idempotent concurrent delivery,
  concurrent workers, apply-to-movement crash recovery, source-delete preservation,
  actual SJ creation and PO identity, unit ambiguity, and legacy exclusion.
- The selected stock engine, delivery rules, proof/RBAC, and multicolor regression
  run plus the new suite passed **181 tests** on the selected suites.
- The new pending panel passed **4 component tests** (read-only opening, pending
  retry, 403, and deleted-source review). Modified JSX/API files parse with Babel.
- Full frontend build, repository-wide tests and CI have **not** run.

## Required before release

1. Obtain and integrate the actual production tree, preserving later UI releases.
2. Review rollout interaction with legacy delivery claims already held in production.
   A detected same-action legacy fence/holder is left review-required, preventing
   automatic replay as a new debit. Existing synchronous endpoint tests in `test_confirm_delivered_sj_stock.py`
   have not been migrated or claimed green under the asynchronous protocol. Their
   helpers are reused for isolated fixtures only. Do not hide these tests in CI.
3. Validate the full workflow and UI on that integrated tree, including HPP/reporting
   visibility while physical movements remain pending.
4. Review concurrent source cancellation/stock application and the operational
   handling of review-required obligations. No automatic historical replay or
   automatic reversal is provided by this patch.
5. Bound queue growth and monitor latency/load at production scale before enabling
   the worker there. The current worker polls pending IDs every 15 seconds and
   retries database poll failures; it is not a proof of production capacity.

No production data, service, branch, deployment or index migration was changed.
