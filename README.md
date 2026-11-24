# Composio AppScout

## Start here — first things first

This is a small website that shows how easy or hard **100 apps** are to connect to.

**The easiest way:** just open the live site:
https://composioappscout-production.up.railway.app

Click **Full results** in the top menu to see the big table.

### Run it on your own computer

You only need Windows and Python 3.11+. In this folder, open PowerShell and copy
these four lines:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8000
```

Then open http://127.0.0.1:8000 in your browser. That's it.

### What the menu does

- **Home** — the easy-to-read summary.
- **Full results** — the table of all 100 apps.
- **results.json** — the raw results.
- **patterns.json** — the counted patterns.
- **verification.json** — the accuracy numbers.

You do **not** need API keys just to open the website. Keys are only needed if you
want to run the research again; that is explained further down.

## What this project is

An evidence-backed research agent that investigates **100 apps** and reports how
hard they are to integrate: auth methods, credential access, public API surface,
existing MCP servers, buildability and blockers. Every non-`unknown` claim must
have a URL and a snippet from a page that was actually fetched. When evidence is
missing, the answer is `unknown` and the record is marked `needs_human` — never
guessed.

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

## Results and limitations

Final published snapshot:

- 86 of 100 apps have results; 14 remain API-limit errors. Record statuses are
  46 `ok`, 40 `needs_human` and 14 `error`.
- Pass 2 versions: 81 fixed (`subfield-fix-1`), 19 restored v1 (`v1-pre-fix`).
  Only fixed records are scored. 25 records remain marked `retry_pending` after
  capped retries.
- Verification scored 11 of 18 eligible apps. All 20 answer-key entries were
  reviewed by hand, including all 11 scored apps; 0 answer values were
  corrected after review. The scored apps are Podio, Discord, WhatsApp Business,
  Shopify, Amazon Selling Partner, Waterfall.io, Snowflake, Sentry, Notion,
  QuickBooks and Consensus.
- Overall accuracy: pass 1 31/55 (56.4%), pass 2 27/55 (49.1%). By field:
  `auth_methods` 45.5% vs 45.5%, `self_serve` 54.5% vs 54.5%, `api_type`
  54.5% vs 54.5%, `existing_mcp` 72.7% vs 36.4%, `buildability` 54.5% vs
  54.5%. There are 52 miss rows.
- Sherlock and Mermaid CLI are excluded as not hosted APIs. Seven sample apps
  still on API errors are unscored: Attio, Pylon, Gladly, Google Ads,
  GoHighLevel, ClickUp and Brex.
- Pass 2 did not improve accuracy on this sample. The four non-MCP fields score
  identically; the whole drop is `existing_mcp`, where a code rule downgrading
  "official" MCP claims without vendor-domain evidence removed five
  correct answers (Shopify, Sentry, Notion, QuickBooks, WhatsApp Business). The
  cause was not diagnosed and the rule was not tuned after seeing scores.
- MCP counts mix fixed and v1-pre-fix records and are indicative. 13 of the 28
  pass-2 misses are abstentions (agent said unknown), and 12 of the 52 miss rows
  come from Consensus and Waterfall.io. All 20 sample entries are reviewed by
  hand, but with only 11 scored apps, all accuracy numbers remain indicative.

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
- The answer key was AI-drafted from official docs and all 20 sample entries were
  reviewed by hand. Accuracy numbers are computed only from that file and are
  never fabricated or tuned.

See `PROJECT_SPEC.md` for the original build specification and `PROGRESS.md` for
the run log and deployment history.
