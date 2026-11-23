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

## What is needed from the user before publishing

- GitHub: confirm the remote `https://github.com/N0teveryth1ng/composio_AppScout`
  is the intended one, and create the empty repository there (or grant push
  access). No account or remote was created here.
- Host: choose Render or Railway and create the app/service pointing at that
  GitHub repo. Nothing was created here. Render can import `render.yaml`;
  Railway can use the `Procfile`. No environment variables are needed for the
  read-only dashboard.
