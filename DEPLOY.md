# Deployment

The dashboard is read-only. It serves the committed JSON artifacts in `data/`
and needs no API keys at runtime. `.env`, `.env.txt`, `data/cache/`, `data/pass1.json`
and the pass logs are gitignored.

## Local

```powershell
pip install -r requirements.txt
uvicorn app:app --host 127.0.0.1 --port 8000
```

## Render

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`

`render.yaml` already contains these, so a Render Blueprint can be created from
the repo without entering them by hand. No environment variables are required
for the dashboard.

## Railway

- Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`

`Procfile` already contains this command; Railway detects Python and installs
`requirements.txt` automatically. No environment variables are required.

## Data served by the app

| File | Used for | Committed |
|---|---|---|
| `data/results.json` | `/results`, `/results.json`, homepage | yes |
| `data/patterns.json` | homepage headlines, `/patterns.json` | yes |
| `data/tool_usage.json` | backend usage on homepage | yes |
| `data/verification.json` | accuracy numbers, `/verification.json` | only when generated |
| `data/pass1.json`, `data/pass1_frozen.json` | research input, never served | no |

`results.json` currently holds the 10-record pass-2 test output, because pass 1
is still running. After pass 1 finishes and `data/pass1_frozen.json` exists,
refresh the artifacts in this order:

```powershell
.\.venv\Scripts\python.exe agent.py --pass2-only
.\.venv\Scripts\python.exe patterns.py
# after ground_truth.json is filled in by hand:
.\.venv\Scripts\python.exe verify.py --compare
```

Then commit the refreshed `data/*.json` files and push them yourself.
