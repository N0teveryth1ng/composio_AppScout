# Composio AppScout

An evidence-backed research agent that investigates **100 apps** and reports how
hard they are to integrate: auth methods, credential access, public API surface,
existing MCP servers, buildability and blockers. Every non-`unknown` claim must
have a URL and a snippet from a page that was actually fetched. When evidence is
missing, the answer is `unknown` and the record is marked `needs_human` — never
guessed.

**Live dashboard:** https://composioappscout-production.up.railway.app
**Repository:** https://github.com/N0teveryth1ng/composio_AppScout

## What is here

| File | Purpose |
|---|---|
| `apps.json` | The 100 apps, 10 categories × 10 apps (static input) |
| `schema.py` | Fixed record shape, validation, `counted_records()` |
| `tools.py` | Search/fetch via Composio Instant tools, with a free fallback; page cache |
| `llm.py` | Free-tier LLM wrapper (Groq + Gemini), model fallback chain, JSON cache |
| `agent.py` | Pass 1 research, pass 2 wiring, resume/freeze and CLI |
| `pass2.py` | Batched claim check, snippet-in-page check, consistency rules |
| `patterns.py` | Headline patterns from `data/results.json` |
| `verify.py` | Sample picker, empty ground-truth template, accuracy comparison |
| `app.py` + `templates/` | Read-only FastAPI dashboard |
| `data/*.json` | Committed output artifacts the dashboard reads |

Raw research files (`data/pass1.json`, `data/pass1_frozen.json`, `data/cache/`, logs)
and secrets are gitignored. The deployed app needs no API keys.

## Setup

Python 3.11+. Free tiers only — no paid services or credit cards.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

Then fill `.env`:

- `COMPOSIO_API_KEY` — Composio free Hobby key. Enable **Instant Toolkits** in
  project settings and keep the one-time free credit balance.
- `LLM_PROVIDER` / `LLM_MODEL` — default `groq` / `openai/gpt-oss-20b`.
- `LLM_API_KEY` — Groq free-tier key.
- Optional fallbacks: `LLM_FALLBACK_MODELS` (comma-separated Groq models) and
  `GEMINI_API_KEY` / `GEMINI_MODEL` (currently `gemini-3.5-flash-lite`), tried in
  that order on 429/503.
- `LLM_DELAY_SECONDS`, `MAX_PAGES_PER_APP` — optional tuning.

Both page fetches and LLM responses are cached on disk, so re-runs are cheap.
Failures are never cached.

## Run the research

```powershell
.\.venv\Scripts\python.exe agent.py --limit 5                  # smoke test
.\.venv\Scripts\python.exe agent.py --apps Salesforce,Stripe   # selected apps
.\.venv\Scripts\python.exe agent.py --all                      # all 100
.\.venv\Scripts\python.exe agent.py --pass2-only               # verify saved pass 1
```

`--all` writes after every app, skips completed records and retries `error`
records on the next invocation. Backend calls are logged to `data/tool_usage.json`.
`--all` retries apps in `data/sample.json` first. Once all 100 records exist and
every sample app is non-error, the agent creates `data/pass1_frozen.json` once —
the frozen baseline used for accuracy measurement. Non-sample apps may still be
in `error`; those records are excluded from statistics. Never edit that file.

Pass 1 status depends only on auth, API type and self-serve; a `main_blocker`
without official evidence is left `unknown` without changing the status.
`main_blocker` covers API access only — plan-limited features are not `paid_only`.

## Refresh the published data

After pass 1 is complete and frozen, run in this order:

```powershell
.\.venv\Scripts\python.exe agent.py --pass2-only
.\.venv\Scripts\python.exe patterns.py
```

The answer key in `data/ground_truth.json` was AI-drafted from official docs,
pricing and sign-up pages; it is gitignored, and human review is tracked with
`human_reviewed` flags. Sherlock and Mermaid CLI are reported separately and
excluded from accuracy because they are not hosted APIs. Measure the real
pass-1/pass-2 accuracy:

```powershell
.\.venv\Scripts\python.exe verify.py --compare
```

Commit the refreshed `data/results.json`, `data/patterns.json` and
`data/verification.json`; Railway auto-deploys on every push to `main`.

## Web app

```powershell
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

| Route | Content |
|---|---|
| `/` | Case study: headline patterns, easy wins, needs-outreach, method, needs-human list, verification |
| `/results` | Full table with filters for category, auth, self-serve, buildability, status |
| `/results.json` | Raw final records |
| `/patterns.json` | Computed patterns |
| `/verification.json` | Accuracy numbers (404 until generated) |

## Deployment

Read-only FastAPI app; no runtime secrets or database.

- Render: `render.yaml` (build `pip install -r requirements.txt`, start
  `uvicorn app:app --host 0.0.0.0 --port $PORT`)
- Railway: `Procfile` with the same start command

`app.py` resolves `data/*.json` relative to its own directory, so deployment works
regardless of the working directory. See `DEPLOY.md` for details.

## Honesty notes

- API-error records are excluded from statistics and retried; they are never
  reported as research findings.
- JavaScript-heavy docs can yield no text; those apps legitimately become
  `needs_human`.
- The answer key was AI-drafted from official docs and marked for human review.
  Accuracy numbers are computed only from that file and are never fabricated or tuned.

See `PROJECT_SPEC.md` for the original build specification and `PROGRESS.md` for
the run log and deployment history.
