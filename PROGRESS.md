# Progress

Background `--all` is running separately. Do not edit `data/pass1.json` or touch
that process. `data/pass1_frozen.json` is created once by the agent when all 100
apps are done and is never edited afterwards.

- [x] Confirm `GEMINI_API_KEY` is loaded and in the fallback chain (answer: yes)
- [x] Document how to check/relaunch the background run
- [x] Build pass 2 (`--pass2-only`): batched claim check, snippet-in-page check, consistency rules; test on the 10 existing records only (8 ok / 2 needs_human / 0 error, validation clean). Partial output copied to `data/results.json`.
- [x] Build `patterns.py`, `verify.py` (sample picker + empty ground-truth template), FastAPI `/` and `/results`; tested on partial data (patterns: 10 counted; sample: 20 apps; web: `/`, `/results`, filters, JSON routes all pass)
- [x] Start and maintain this progress log

## Pass 2 test note

- Gemini key in `.env.txt` was copied into `.env`; `gemini-2.5-flash-lite` is no
  longer available, so `GEMINI_MODEL=gemini-3.5-flash-lite` was set in `.env`
  and `.env.example`. Several calls returned transient HTTP 503; `pass2.run()`
  retries error-status records on the next invocation, so the test was re-run
  until all 10 were verified.

## Notes

- Ground truth is filled by the user; only the empty template is generated here.
- Pass 2 is not run on the full set until pass 1 finishes and is frozen.
- An attempted `--pass2-only` CLI smoke test used `pass2.run()`'s bound default
  paths and briefly verified part of the live pass-1 file into `data/results.json`.
  That file was immediately restored to the 10-record partial test output; no
  full pass-2 run has been done and `pass1.json` was never written.
- `verify.py` wrote `data/sample.json` (20 apps, seed 42, 2 per category) and an
  empty `data/ground_truth.json`. It was NOT filled and `--compare` was NOT run.
- FastAPI tested with `TestClient`: `/` 200, `/results` 10 rows,
  `/results?status=needs_human` 2 rows, `/results.json` and `/patterns.json` 200,
  `/verification.json` 404 until verification exists.

## Relaunch with Gemini (2026-11-23 20:03)

- Live check before relaunch: `gemini-3.5-flash-lite` returned valid JSON
  (`GEMINI_OK {"ok": true, "model": "gemini-3.5-flash-lite"}`), so the model was kept.
- Old processes (PIDs 17124, 22636) stopped; detached relaunch PID 21924
  (parent 27980, so not tied to the OpenCode session).
- Fresh chain: `groq:openai/gpt-oss-20b`, `groq:openai/gpt-oss-120b`,
  `groq:qwen/qwen3.8-27b`, `gemini:gemini-3.5-flash-lite`.
- New records produced after relaunch: Aircall (error, transient 503),
  SE Ranking (ok: qwen + gemini + gpt-oss-120b), Ahrefs (ok: gemini only),
  MrScraper (ok: gemini only). 4 records now carry a Gemini model.
- Pass-1 file at check time: 54 records, 34 ok / 19 needs_human / 1 error.
  Aircall needs another run to retry its transient 503.

## Background run: check and relaunch

- Log: `Get-Content data\pass1_run.log -Tail 20` (or the stdout file shown when
  the background command was launched). One line per app: `app=… status=… model=…`.
- Process: `Get-CimInstance Win32_Process -Filter "Name like '%python%'" |
  Where-Object { $_.CommandLine -like '*agent.py --all*' } | Select ProcessId,ParentProcessId,CommandLine`
- If it dies: just run `python agent.py --all` again. It skips finished apps,
  retries `error` records, and writes after every app. Nothing is lost.
- The launch is tied to this OpenCode session. To detach it as its own Windows
  process, first confirm no `agent.py --all` process is running, then:
  `cd D:\composio_term; $env:LLM_DELAY_SECONDS='5'; Start-Process -FilePath 'D:\composio_term\.venv\Scripts\python.exe' -ArgumentList 'agent.py --all' -WorkingDirectory 'D:\composio_term' -WindowStyle Hidden -RedirectStandardOutput 'data\detached.out' -RedirectStandardError 'data\detached.err' -PassThru | Select-Object Id`
  (ParentProcessId should then be your shell, not OpenCode.)

## Deployment preparation (pass 1 still running)

- [x] `git init -b main` in `D:\composio_term` (local only). No remote added and
  nothing pushed. A parent repo exists at `D:\`, but the local repo is the one
  used here; no global git config was changed.
- [x] `.gitignore` hardened and verified with `git check-ignore -v`. Ignored:
  `.env`, `.env.txt`, other `.env.*` (except `.env.example`), `.venv/`,
  `__pycache__/`, `*.pyc`, `data/cache/` (1333 cached page files),
  `data/pass1.json`, `data/pass1_frozen.json`, `data/pass1_run.log`,
  `data/detached.out`, `data/detached.err`, `data/ground_truth.json`.
- [x] Secret scan of every staged file: no API-key-like tokens found.
- [x] `requirements.txt` checked against `app.py`: `fastapi` is the only direct
  third-party import; `jinja2` (templates) and `uvicorn` (start command) are
  also listed. No missing packages.
- [x] `app.py` now resolves `data/*.json` relative to the app file, not the
  working directory. Verified from a different CWD with `TestClient`: `/`,
  `/results`, `/results.json`, `/patterns.json` 200; `/verification.json` 404
  until that artifact is generated.
- [x] Added `Procfile` (Railway), `render.yaml` (Render Blueprint) and
  `DEPLOY.md`. Start command on both: `uvicorn app:app --host 0.0.0.0 --port $PORT`.
  Build command on Render: `pip install -r requirements.txt`. No runtime keys.
- [x] Local initial commit created with the committed artifacts
  `data/results.json`, `data/patterns.json`, `data/sample.json`,
  `data/tool_usage.json`. `results.json` is still the 10-record partial pass-2
  output; refresh it after pass 1 freezes.

## GitHub push and Railway check (2026-11-23)

- GitHub CLI is authenticated as `N0teveryth1ng`; `composio_AppScout` existed but
  was **empty** (GitHub API: `isEmpty: true`, contents 404). Added `origin` and
  pushed `main` (`9dd04e5`). Contents API now lists all 23 committed files.
- No Railway CLI is installed, no Railway auth config exists locally, the in-app
  browser shows Railway's login dialog, and the repo has zero GitHub
  deployments and zero webhooks. So Railway is **not yet connected** to this
  repo as far as can be verified from here.
- Verified the exact start command locally with uvicorn: `/`, `/results`,
  `/results.json`, `/patterns.json` all returned 200.

## Railway deployment live (2026-11-23)

- User logged into GitHub in the in-app browser; Railway dashboard then showed
  the `bubbly-illumination` project with a `composio_AppScout` service already
  pointed at `N0teveryth1ng/composio_AppScout` (branch `main`), but with 3
  unapplied changes and no active deployment.
- Applied the changes (repo + branch + redeploy). Deployment `4b89b4ab` became
  ACTIVE; auto-deploy on GitHub push is enabled.
- Generated public domain `https://composioappscout-production.up.railway.app`
  on target port 8080 (matches Uvicorn's `$PORT` in the deploy logs).
- Verified live: `/` 200 with all homepage sections, `/results.json` 200 with
  the 10 committed pass-2 records. No runtime env vars or keys are set.
- Pass 1 is still running (64 records at check time: 35 ok / 19 needs_human /
  10 error); the live site therefore still shows the 10-record partial dataset.

## README (2026-11-23)

- [x] Added `README.md` with the live link, repo layout, free-tier setup, CLI
  run modes, data-refresh order, verification flow, web routes, deploy commands
  and honesty notes. Committed and pushed; Railway auto-deploys on the push.

## Backend-calls table and secret scan (2026-11-23)

- [x] `app.py` now builds `usage_rows` and `/` renders backend calls as a small
  table (operation / backend / OK / Failed) instead of raw JSON. One-line note
  on the page: most failures are guessed official URLs that don't exist; they
  are logged, never used as evidence, and do not affect record status. Verified
  with `TestClient` from the live `tool_usage.json`.
- [x] Key-like scan (`gsk_`, `ak_`, `AIza`) over the working tree (including
  gitignored files, excluding `.venv`/`.git`), tracked files at HEAD, all 4
  commits' contents and all commit messages: only `.env` and `.env.txt` contain
  matches (1 `gsk_`, 1 `ak_` each); they are gitignored. Zero tracked/history
  hits, zero `AIza` hits. No values were printed.

## Pass 1 finished and live-data refresh (2026-11-23)

- Pass 1 ended on its own at 21:59:35 with all 100 records written:
  42 ok / 22 needs_human / 36 error. The detached process was not stopped.
- `data/pass1_frozen.json` was **not** created: the agent only freezes when all
  100 records exist and none has `status=error`; 36 transient API-error records
  remain. No manual freeze was forced.
- Started a fresh pass 2 from a stable read-only snapshot of `pass1.json`,
  with repeat sweeps that retry error records. It skips the 10 already-verified
  records and re-verifies every error record.
- `/` now states "X of 100 apps covered" (count of records in `results.json`)
  plus counted vs API-error counts; verified with `TestClient`.

## Pass-1 error analysis (2026-11-23)

- All 36 pass-1 `error` records have the same cause: transient
  `LLM_API_ERROR: ServerError: 503 UNAVAILABLE` (free-tier model overloaded).
  No data/evidence error, no schema error.
- `llm_model` on those records: 24 `gemini-3.5-flash-lite` alone; the other 12
  are two-model chains that also ended on `gemini-3.5-flash-lite`.
- 9 of the 36 are in `data/sample.json`: Sherlock, Waterfall.io, Snowflake,
  Sentry, ClickUp, QuickBooks, Brex, Consensus, Mermaid CLI.
- Verified a single retry (`agent.py --apps Sherlock`): Sherlock recovered to
  `needs_human`, so the 503s are transient.
- Stopped the stale pass-2 background job (it used a pre-retry snapshot and
  competed for the same free LLM quota). Pass 2 will be re-run after retries.

## Retry state check (2026-11-23)

State reported to the user: pass 1 = 44 ok / 25 needs_human / 31 error;
`pass1_frozen.json` absent; `agent.py --all` and pass 2 were both running at
the time of the check. Last commit `9bd6210`.

- Sample apps still in `error` (4): ClickUp (77), Brex (88), Consensus (94),
  Mermaid CLI (98). All four are the same transient 503 "model overloaded"
  LLM error. Because sample apps are not all non-error, **no freeze was done**.
- `agent.py --all` was stopped (it was retrying the same overloaded models and
  blocking a clean pass 2). Pass 2 was started over all 100 pass-1 records with
  sample apps first.
- Ground truth: 20 entries, 0 filled by hand. No accuracy numbers invented;
  `verify.py --compare` was not run and `verification.json` is not created.

## Brex resolved and baseline frozen (2026-11-24)

- Brex was retried 3 more times; it recovered on the fourth extra attempt
  (`status=ok`, `openai/gpt-oss-20b`, 9 evidence items). No sample change was
  needed — all 20 sample apps are now non-error.
- `agent.py` auto-froze `data/pass1_frozen.json`: 100 records,
  46 ok / 27 needs_human / 27 error. Non-sample error records remain and are
  excluded from statistics.
- Full pass 2 is running over all 100 records with sample apps first.

## Ground truth prefilled with sources (2026-11-24)

- By explicit user request, `data/ground_truth.json` (gitignored, user-owned)
  now has `doc_url` + `passage` for all 20 sample apps. Every passage is real
  text verified against a cached first-party docs page (the one exception,
  Sherlock, was fetched live from the official `sherlock-project/sherlock`
  README). First-party domains include `docs.attio.com`, `developers.podio.com`,
  `docs.usepylon.com`, `developer.gladly.com`, `docs.discord.com`,
  `developers.facebook.com`, `developers.google.com`, `shopify.dev`,
  `developer-docs.amazon`, `docs.snowflake.com`, `docs.sentry.io`,
  `developers.notion.com`, `developer.clickup.com`, `developer.brex.com`,
  `docs.consensus.app`, plus official GitHub/npm repos.
- That prefilled file was later drafted with truth values from those same
  official sources; see "Ground truth drafted" below. `verify.py --compare`
  remains withheld until the human-review count is confirmed.

## Ground truth drafted (2026-11-24)

- At the user's request, the five truth fields for all 20 sample apps were
  drafted from official docs/pricing/sign-up pages only, with every URL used
  recorded under `urls`, `drafted_by: "ai"` and `human_reviewed: false`.
- The user asked that Sherlock and Mermaid CLI be excluded from accuracy because
  they are not hosted APIs, and listed separately on the page.
- `verify.py --compare` is deliberately withheld until the user supplies the
  confirmed human-review count. The measured count in the file at this point is
  0 of 20 `human_reviewed: true`, so no accuracy numbers are published.

## Final error retries and pass 2 (2026-11-24)

- Retried the 27 non-sample pass-1 `error` apps up to 3 attempts each.
  26 recovered; Clay still failed on all 3 attempts with transient 503s.
  Pass 1 finished at 66 ok / 33 needs_human / 1 error.
- Re-ran pass 2 with `agent.py --pass2-only` over the refreshed pass-1 data.
  Final `data/results.json`: 100 records, 40 ok / 47 needs_human / 13 error.
  The 13 remaining pass-2 errors are transient LLM-verification 503s, not
  missing research; they are excluded from patterns.
- `patterns.py` regenerated `data/patterns.json`: 87 counted, 13 excluded;
  28 easy wins, 3 need outreach, 54 official and 11 community MCP servers.

## Dashboard restyle (2026-11-24)

- Added `templates/_styles.html` with a single system-font stylesheet: light
  grey-blue page, one centred white card, pill filters, soft status/access/
  buildability badges, sticky table header, horizontal table scroll, and
  comfortable dividers instead of heavy borders.
- `templates/results.html` now has a `Sources (N)` cell with one link per
  unique source domain, opening in a new tab, instead of one link per field.
- `templates/index.html` sections are simple cards; headline patterns are a
  short list and easy wins/needs outreach are pill chips.
- Both pages were rendered at a 390px iframe viewport: no page-level horizontal
  overflow; the table scrolls inside its card. No JS, external fonts or libraries.

## Publishing

- GitHub repo: `https://github.com/N0teveryth1ng/composio_AppScout`
- Railway project `bubbly-illumination`, service `composio_AppScout`, public URL
  `https://composioappscout-production.up.railway.app`
- Verification remains pending by explicit request; `verification.json` is not
  generated or committed.
