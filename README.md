# nansen

Cache-first research harness for testing whether Nansen Smart-Money accumulation predicts forward returns, especially whether accumulation **before** price movement outperforms buying after a large move is already underway.

## Install from GitHub on Zorin

Use the repository rather than a ZIP:

```bash
cd ~/Téléchargements
DEST="$HOME/Téléchargements/nansen-signal-lab"
if [ -e "$DEST" ]; then
  mv "$DEST" "${DEST}.failed-$(date +%Y%m%d-%H%M%S)"
fi
git clone https://github.com/u-dont-existDOTcom/nansen.git "$DEST"
cd "$DEST"
bash bootstrap.sh
```

The bootstrap:

1. creates a local Python virtual environment;
2. installs pinned-compatible dependencies;
3. asks for the Nansen API key with hidden terminal input;
4. stores the key only in `.env` with restrictive permissions;
5. runs the test suite;
6. performs one cached Token Screener smoke call.

`.env`, API caches, result files, virtual environments and Python bytecode are excluded from Git.

## Commands

```bash
./nansen-lab smoke
./nansen-lab plan --tokens 10
./nansen-lab candidates
./nansen-lab flows --chain solana --token TOKEN_ADDRESS --days 7
./nansen-lab flows --chain solana --token TOKEN_ADDRESS --label exchange --days 7
./nansen-lab who-bought-sold --chain solana --token TOKEN_ADDRESS --side BUY \
  --from 2026-08-16T00:00:00Z --to 2026-08-17T00:00:00Z
```

Identical requests are cached under `data/cache/`. Use `--refresh` only when you deliberately want another paid/current API call.
Flow responses written to the default ignored `results/` scratch path retain the historical overwrite behavior. An explicit `flows --output PATH` is treated as durable evidence and refuses to replace either the response or its `.request.json` sidecar unless `--force-output` is also supplied. New sidecars distinguish cache hits, original response retrieval time, artifact write time, and the exact response SHA-256. Legacy raw-only cache entries use their file modification time as the retrieval-time fallback instead of claiming a fresh network response.

`who-bought-sold` records wallet-level buyer or seller evidence for an explicit interval, labels, threshold, and page. It is the required source for buyer breadth, subject to complete pagination; `holders_count` is only holder breadth. `flows --label exchange` archives exchange-labelled flow evidence separately from Smart-Money flows. Neither command runs automatically: live/current requests can consume credits only when you explicitly invoke one. A paired exchange flow and Smart-Money flow does not establish transfer attribution.

## Research architecture

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The initial experiment compares early Smart-Money accumulation with momentum accumulation and eventually an eligible-token control cohort, while explicitly avoiding look-ahead and survivorship bias.

This repository is the canonical durable checkpoint for the workflow.

## Committed research evidence

The first public evidence bundle is the [`2026-08-16 seven-token pilot`](research/experiments/2026-08-16-seven-token-pilot/REPORT.md). Reproduce its deterministic analysis with:

```bash
./nansen-lab analyze --manifest research/experiments/2026-08-16-seven-token-pilot/manifest.json --check
./nansen-lab analyze --manifest research/experiments/2026-08-16-community-signal-shadow/manifest.json --check
./nansen-lab evaluate --manifest research/experiments/2026-08-17-paper-strategy-feasibility/manifest.json --check
```

The [`community-signal shadow`](research/experiments/2026-08-16-community-signal-shadow/REPORT.md) is a schema-v2, discovery-only companion derived from the immutable schema-v1 pilot. It uses `bucket_end` as feature availability, contains no fitted score, and cannot be used as a holdout while its point-in-time guarantee is unknown.

The [`paper-strategy feasibility evaluation`](research/experiments/2026-08-17-paper-strategy-feasibility/REPORT.md) is a schema-v3 offline comparison of preregistered theories. It selected no entry strategy, so it emitted no paper shortlist; its holder-breadth comparison advanced only as a descriptive follow-up. The next evidence upgrade is a separately versioned historical/beta collection with complete point-in-time provenance, pagination, liquidity, quotes, and execution costs.

The bounded historical-discovery workflow tests that holder-breadth lead on a
fixed 20-token, eight-week daily cohort before a larger prospective spend. Init
and check are offline; start is the only live step and enforces the fixed
nine-request-attempt/twelve-credit ceiling:

```bash
./nansen-lab historical-init --experiment-dir research/experiments/EXPERIMENT_ID
./nansen-lab historical-start --manifest research/experiments/EXPERIMENT_ID/manifest.json
./nansen-lab historical-check --manifest research/experiments/EXPERIMENT_ID/manifest.json
```

This route uses a temporally anchored beta screener, point-in-time daily
historical holdings, and independent chain-batched daily OHLCV outcomes. It is
discovery-only: daily close proxies, liquidity known only at selection, and
fixed cost sensitivities cannot establish an executable or profitable strategy.

The first bounded discovery sealed `unscorable` after the paid screener omitted
its quoted-cost header. Its one-off source-bound recovery adopted those exact
bytes, froze the audited `bsc`-to-`bnb` normalization and four outcome batches
offline, and never called the screener again. The recovery completed at seven
additional authenticated attempts and six additional credits; including the
source, the study used nine attempts and eleven credits. The exact daily rule
did not advance because its event-median spread was negative. Both bundles are
terminal; verify the recovery offline, but do not initialize or start another:

```bash
./nansen-lab historical-recovery-check \
  --manifest research/experiments/2026-08-18-holder-breadth-historical-recovery-v2/manifest.json
```

The next experiment family is a separate 32-cycle prospective cohort. It uses
five deterministic strata per cycle, complete Smart-Money/exchange flow and
buyer/seller breadth, frozen pre-entry decisions, and the same counterfactual
DEX/OHLCV outcome for every selected token—including abstentions. The full
program is an untouched holdout; it never trains a replacement rule from its
own returns. Its sole confirmatory rule is buyer-breadth/exchange co-movement
paired with the frozen distribution veto; every other rule is descriptive.
Planning, initialization, replay, checking, and final aggregation are offline.
Only the two per-cycle commands can access Nansen:

```bash
# Offline budget calculation: 1,792 credits maximum, 1,824 authenticated attempts.
./nansen-lab cohort-plan

# Initialize only after the implementation is committed and the operator has
# funded the full ceiling. The first live cycle's zero-credit preflight proves
# all 1,792 credits before its first billable call. The first time is HH:05 UTC.
./nansen-lab cohort-init \
  --experiment-dir research/experiments/PROSPECTIVE_COHORT_ID \
  --first-cycle-at 2026-08-24T12:05:00Z

# Live, schedule-bound collection and settlement at the exact sealed boundary.
./nansen-lab cohort-start-cycle --program research/experiments/PROSPECTIVE_COHORT_ID/program.json --cycle 1
./nansen-lab cohort-settle-cycle --program research/experiments/PROSPECTIVE_COHORT_ID/program.json --cycle 1

# Offline integrity/progress and terminal-only aggregation.
./nansen-lab cohort-replay --program research/experiments/PROSPECTIVE_COHORT_ID/program.json
./nansen-lab cohort-check --program research/experiments/PROSPECTIVE_COHORT_ID/program.json
./nansen-lab cohort-finalize --program research/experiments/PROSPECTIVE_COHORT_ID/program.json
```

The maximum assumes two pages on every buyer/seller and DEX surface; typical
one-page use is 1,152 credits. A cycle cannot reroll a token, retry a request,
or use a second screener page. The observed DEX VWAP with 100/250-bps costs is
execution-aware paper evidence, not an executable route. With only 160 total
opportunities, the cohort may validly finish `insufficient_strategy_fills`;
counterfactual fills for `ABSTAIN` never count toward the 100 strategy-fill
advancement gate. A decision seal must precede its first entry boundary and
land within 45 minutes of the scheduled start, so slow or stale recovery cannot
turn old features into a backtest.

Initialization archives self-contained frozen comparator definitions, exact
copies plus hashes of the protocol runtime, and exact Python/dependency
versions. Every live command and offline replay refuses environment drift, so
selection, decisions, execution scoring, and aggregation cannot change
outcome-adaptively during the eight-week holdout.

The active cohort is advanced by the external
`scripts/prospective_cohort_timer.py` supervisor and the versioned user units in
`ops/systemd/`. The timer wakes every minute but makes no call before an action
is due. It requires an NTP-synchronized clock, waits for NetworkManager before
live work, uses the absolute manifest path, resumes crash-safe stages, and reads
the exact settlement boundary from the sealed decision artifact. It never
rerolls or edits the frozen implementation. Install and enable the versioned
units with:

```bash
install -Dm0644 \
  /mnt/hdd/home/joel/Téléchargements/nansen-signal-lab/ops/systemd/nansen-signal-lab-cohort.service \
  /home/joel/.config/systemd/user/nansen-signal-lab-cohort.service
install -Dm0644 \
  /mnt/hdd/home/joel/Téléchargements/nansen-signal-lab/ops/systemd/nansen-signal-lab-cohort.timer \
  /home/joel/.config/systemd/user/nansen-signal-lab-cohort.timer
systemctl --user daemon-reload
systemctl --user enable --now nansen-signal-lab-cohort.timer
loginctl enable-linger "$USER"
```

Verify the installed unit, next poll, linger state, and service log with:

```bash
systemctl --user status nansen-signal-lab-cohort.timer
systemctl --user list-timers nansen-signal-lab-cohort.timer
loginctl show-user "$USER" -p Linger
journalctl --user -u nansen-signal-lab-cohort.service
```

The machine must remain powered and awake around collection windows. User
lingering keeps the timer active after logout and starts the user manager during
boot; `Persistent=true` catches the next poll after downtime but cannot recreate
market observations that were missed while the machine was off.

The [`prospective GPT pilot`](research/experiments/2026-08-17-gpt-prospective-pilot/REPORT.md) is an immutable terminal schema-v4 observation. Its public Nansen contract preflight matched, but its OpenAI model-access preflight returned HTTP 401 before token selection or GPT inference; the result is `unscorable`, with zero Nansen calls and zero Nansen credits. The [terminal-audit erratum](docs/audits/2026-08-17-gpt-prospective-pilot-erratum.md) marks it unusable for model or strategy comparison because the wrong credential class should have failed local validation. The bundle cannot create an order, wallet action, venue submission, or capital movement and will not be rerun or rewritten.

The [`prospective GPT pilot successor`](research/experiments/2026-08-17-gpt-prospective-pilot-successor/REPORT.md) is a separately named terminal schema-v4 observation. The corrected OpenAI key passed the exact `gpt-5.6-sol` model-access preflight, but Nansen's free account response omitted the declared credit-use and remaining-balance headers, so the protocol stopped before selection or inference. Its [result review](docs/audits/2026-08-17-gpt-prospective-pilot-successor-result-review.md) explains why replay conservatively records one ambiguous call/credit even though the received cost header was zero.

```bash
./nansen-lab pilot-replay --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
./nansen-lab pilot-check --manifest research/experiments/2026-08-17-gpt-prospective-pilot/manifest.json
```

`results/` is ignored scratch space for exploratory outputs. Evidence becomes durable only after it is copied byte-for-byte into `research/experiments/`, checksummed in a manifest, documented, and committed. The append-only [`research ledger`](docs/RESEARCH-LEDGER.md) and [`evidence graph`](docs/RESEARCH-GRAPH.md) preserve the experiment's claims, limitations, and follow-up state.

[Powered by Nansen API](https://nansen.ai/). Public Token Screener and `tgm/flows` evidence is included under Nansen's [redistribution guidance](https://docs.nansen.ai/guides/redistribution-guide).
