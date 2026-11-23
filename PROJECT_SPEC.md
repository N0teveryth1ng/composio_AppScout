# PROJECT SPEC: App Research Agent (Composio take-home)

Read this whole file first, then build the project step by step in the order given in "Build Order". After each step, run it and confirm it works before moving on.

## 0. Ground rules (IMPORTANT)

- Language: **Python 3.11+**.
- **Free tools only.** No paid services, no credit card. Composio free Hobby plan + a free LLM tier.
- **Simple and functional.** No polish, no production hardening, no fancy UI, no tests framework, no Docker, no auth, no database server. Plain files (JSON) are the storage. Priority is: it runs end to end and produces correct data.
- Keep code small and readable. One file per concern. I (the user) must be able to explain every line in an interview, so avoid clever abstractions.
- Never let the agent guess. If it cannot find evidence, the field is `unknown` and the record status is `needs_human`.
- Cache everything (fetched pages, LLM outputs) on disk so re-runs are free and fast.
- Be honest in all outputs: failures and "needs_human" apps are valid findings and must be shown, not hidden.

## 1. Background

Composio turns apps into tools AI agents can call. Before building a toolkit for an app they research: what auth it uses, whether credentials are self-serve or gated (paid plan / admin approval / partnership / contact sales), how broad the public API is, and whether an MCP server exists or one could be built. This project automates that research for a fixed list of **100 apps** and presents the findings.

## 2. What we are building (deliverables)

1. **Research agent** (`agent.py`): researches all 100 apps automatically and writes `data/results.json`. Uses the **Composio Python SDK** for its tool calls (web search / page scraping), with a plain fallback.
2. **Verification** (`verify.py`): hand-checked sample of ~20 apps, compares to agent output, records accuracy for **pass 1** and **pass 2**.
3. **Patterns** (`patterns.py`): computes the insights (auth distribution, self-serve vs gated by category, common blockers, easy wins vs outreach needed) into `data/patterns.json`.
4. **Web app** (`app.py`, FastAPI):
   - `GET /` : single explainer page (case study): patterns at the top, results summary, how the agent works, where a human was needed, verification results with hits and misses.
   - `GET /results` : the full table of 100 apps (HTML table with simple filters by category, auth type, self-serve/gated, buildability) and `GET /results.json` for raw JSON (so both humans and agents can consume it).
5. **README.md**: how to set up and run the agent, verification and web app.
6. Deployed on a free host (Render or Railway) with a live link, plus a GitHub repo.

## 3. Repo layout

```
.
├── PROJECT_SPEC.md
├── README.md
├── requirements.txt
├── .env.example            # COMPOSIO_API_KEY, LLM_API_KEY, LLM_PROVIDER, LLM_MODEL
├── apps.json               # the 100 apps (static input, see section 9)
├── schema.py               # record shape + validation
├── tools.py                # search + fetch (Composio first, requests fallback)
├── llm.py                  # tiny wrapper around the free LLM
├── agent.py                # research pipeline (pass 1 + pass 2)
├── verify.py               # accuracy measurement
├── patterns.py             # clustering / insights
├── app.py                  # FastAPI: /, /results, /results.json
├── templates/              # 2 simple Jinja templates (index.html, results.html)
└── data/
    ├── cache/              # fetched pages + llm outputs, keyed by app + url hash
    ├── pass1.json          # first-pass output
    ├── results.json        # final output (after pass 2)
    ├── ground_truth.json   # hand-checked sample (filled by the user)
    ├── verification.json   # accuracy numbers for pass 1 vs pass 2
    └── patterns.json
```

## 4. Record schema (fixed, every app returns this exact shape)

```json
{
  "id": 1,
  "app": "Salesforce",
  "category": "CRM and Sales",
  "one_liner": "string, what the app does",
  "auth_methods": ["OAuth2", "API key", "Basic", "Token", "Other", "unknown"],
  "self_serve": "self_serve | trial_or_paid | gated | unknown",
  "self_serve_note": "short reason, e.g. free dev sandbox, needs paid plan, partner approval",
  "api_surface": {
    "type": ["REST", "GraphQL", "SOAP", "SDK only", "none", "unknown"],
    "breadth": "broad | medium | narrow | unknown",
    "existing_mcp": "official | community | none_found | unknown",
    "mcp_url": "url or null"
  },
  "buildability": "yes | partial | no | unknown",
  "main_blocker": "none | partner_gated | paid_only | no_public_api | admin_approval | contact_sales | auth_complexity | other",
  "blocker_note": "short explanation",
  "evidence": [
    {"field": "auth_methods", "url": "https://...", "snippet": "short quote or paraphrase from the page"}
  ],
  "has_composio_toolkit": "yes | no | unknown",
  "status": "ok | needs_human",
  "needs_human_reason": "string or null",
  "pass": 1
}
```

Rules:
- Every non-`unknown` value in `auth_methods`, `self_serve`, `api_surface`, `buildability` must be backed by at least one `evidence` entry (URL + snippet) from a page that was actually fetched in this run.
- If a required field cannot be backed by evidence, set it to `unknown` and set `status = needs_human` with a reason.
- Validate with simple Python checks in `schema.py` (no heavy libraries needed; pydantic is fine if easier).

## 5. Tools layer (`tools.py`)

Goal: two functions the agent uses.
- `search(query) -> list[{title, url, snippet}]`
- `fetch(url) -> str` (clean text/markdown of the page, truncated to a sane length, cached on disk)

Implementation order:
1. **First choice: Composio Python SDK** (`pip install composio`). Use the Composio free Hobby plan. Look up the CURRENT Composio Python SDK docs (docs.composio.dev) to find the correct way to initialise the client and execute tools. Do not guess the API; read the docs. Find a search toolkit and a web-scrape/fetch toolkit that work on the free plan with the fewest extra requirements (some toolkits need their own third-party API key; test one call of each early and pick ones that work). If an official Composio MCP route is simpler for search/fetch, that is also acceptable.
2. **Fallback (must exist)**: if a Composio call fails, is rate limited, or returns empty, fall back to plain `requests` + `beautifulsoup4`/`trafilatura` for `fetch`, and a free search source for `search` (e.g. the `ddgs` DuckDuckGo package). The agent must never crash on one failed call: log it, fall back, and if still failing mark the app `needs_human`.
3. Log which backend served each call (`composio` or `fallback`). We will report this on the page.
4. JavaScript-heavy docs sites (Stoplight, SPA docs) may return empty content with plain fetch. If a Composio scrape toolkit renders JS, prefer it for those. Otherwise accept `needs_human`.

## 6. LLM layer (`llm.py`)

- Free tier only. Default to **Gemini free tier** (`google-genai`) or **Groq free tier**; choose via env `LLM_PROVIDER` and `LLM_MODEL`. Keep the wrapper to one function: `complete_json(system, user) -> dict` that asks for JSON output and retries once on invalid JSON.
- Rate limits matter: add a configurable delay between calls, exponential backoff on 429, and low concurrency (2 workers max).
- Cache every LLM output on disk keyed by a hash of the prompt.

## 7. Agent pipeline (`agent.py`)

For each app in `apps.json`:

**Step A: gather sources**
- Run 3 to 4 searches per app with queries like:
  - `{app} API authentication OAuth API key docs`
  - `{app} developer API getting started get credentials`
  - `{app} API pricing plan required developer access`
  - `{app} MCP server`
- Pick the top relevant URLs (prefer official domains, the hint URL from `apps.json`, developer docs, pricing/plan pages). Fetch up to ~5 pages per app. Store text in cache.
- Also check whether a Composio toolkit exists for the app (look at Composio's public toolkit/docs listing if reachable) and fill `has_composio_toolkit`, otherwise `unknown`.

**Step B: PASS 1 (first pass, single LLM call per app)**
- Give the LLM only the fetched page texts (with their URLs) and the schema. Instruct: "Use ONLY the provided text. For every field give a URL and a short supporting snippet. If the text does not say, return unknown."
- Output goes to `data/pass1.json`. **Do not improve pass 1 after the fact.** It is the baseline we measure first.

**Step C: PASS 2 (verification loop, this is what improves accuracy)**
For each app, run a second independent check:
1. **Claim check**: for each claim in pass 1 (auth, self-serve, API type, MCP, buildability), a second LLM call re-reads the cited snippet/page and answers `supported | contradicted | not_found`. Anything not `supported` is downgraded to `unknown` or corrected.
2. **Evidence check in code (no LLM)**: verify that each cited snippet text actually appears in the fetched page text (fuzzy match is fine). If not found, drop that evidence and the claim depending on it.
3. **Targeted re-search**: for fields that became `unknown`, run one more targeted search (e.g. `{app} API authentication`, `{app} developer sign up free`) and retry once.
4. **Consistency rules** (simple Python): e.g. `self_serve = gated` implies `buildability` cannot be `yes`; `auth_methods` empty implies `buildability = unknown`; no evidence implies `status = needs_human`.
5. Write the final records to `data/results.json` with `pass: 2`.

**Needs-human policy**: apps that still have unknown core fields after pass 2, or whose docs could not be fetched, get `status = needs_human` and a clear `needs_human_reason`. Keep a counter and list of these; the page must show them honestly.

**Run modes** (CLI args):
- `python agent.py --limit 5` (test on the first 5 apps)
- `python agent.py --apps Salesforce,Stripe` (specific apps)
- `python agent.py --all` (all 100)
- `python agent.py --pass2-only` (reuse cached pass 1)

## 8. Verification (`verify.py`)

Purpose: prove the data is trustworthy.
1. Pick a **stratified sample of ~20 apps** (2 per category), save the list to `data/sample.json`. Use a fixed random seed.
2. `data/ground_truth.json` is filled **by the user by hand** from real docs (fields: auth_methods, self_serve, api type, existing_mcp, buildability, plus the doc URL used). Generate a template file with the sample apps and empty fields to make this easy for the user.
3. Compare `pass1.json` and `results.json` (pass 2) against ground truth, per field and overall. Output `data/verification.json`:
   - per-field accuracy for pass 1 and pass 2
   - overall accuracy for pass 1 and pass 2
   - list of every miss: app, field, agent value, true value, likely cause
   - count of `needs_human` apps
4. The numbers must be real. Do not fabricate or tune ground truth. The improvement from pass 1 to pass 2 is reported as measured, even if small.

## 9. The 100 apps (`apps.json` source data)

Create `apps.json` as a list of `{"id", "app", "category", "hint"}`. Data:

**1. CRM and Sales**
1 Salesforce (salesforce.com) | 2 HubSpot (hubspot.com) | 3 Pipedrive (pipedrive.com) | 4 Attio (attio.com) | 5 Twenty (twenty.com, open-source CRM) | 6 Podio (podio.com) | 7 Zoho CRM (zoho.com/crm) | 8 Close (close.com) | 9 Copper (copper.com) | 10 DealCloud (api.docs.dealcloud.com)

**2. Support and Helpdesk**
11 Zendesk (zendesk.com) | 12 Intercom (intercom.com) | 13 Freshdesk (freshdesk.com) | 14 Front (front.com) | 15 Pylon (usepylon.com) | 16 LiveAgent (liveagent.com) | 17 Plain (plain.com) | 18 Help Scout (helpscout.com) | 19 Gorgias (gorgias.com) | 20 Gladly (gladly.com)

**3. Communications and Messaging**
21 Slack (slack.com) | 22 Twilio (twilio.com) | 23 Zoho Cliq (zoho.com/cliq) | 24 Lark (Larksuite) (open.larksuite.com) | 25 Pumble (pumble.com) | 26 Discord (discord.com) | 27 Telegram (core.telegram.org) | 28 WhatsApp Business (developers.facebook.com/docs/whatsapp) | 29 Aircall (aircall.io) | 30 Vonage (developer.vonage.com)

**4. Marketing, Ads, Email and Social**
31 Google Ads (developers.google.com/google-ads) | 32 Meta Ads (developers.facebook.com/docs/marketing-apis) | 33 LinkedIn Ads (learn.microsoft.com/linkedin/marketing) | 34 GoHighLevel (highlevel.stoplight.io) | 35 Mailchimp (mailchimp.com/developer) | 36 Klaviyo (developers.klaviyo.com) | 37 systeme.io (systeme.io, funnel builder) | 38 Pinterest (developers.pinterest.com) | 39 Threads (Meta) (developers.facebook.com/docs/threads) | 40 SendGrid (sendgrid.com)

**5. Ecommerce**
41 Shopify (shopify.dev) | 42 WooCommerce (woocommerce.com/document/woocommerce-rest-api) | 43 BigCommerce (developer.bigcommerce.com) | 44 Salesforce Commerce Cloud (developer.salesforce.com/docs/commerce) | 45 Magento (Adobe Commerce) (developer.adobe.com/commerce) | 46 Squarespace (developers.squarespace.com) | 47 Ecwid (api-docs.ecwid.com) | 48 Gumroad (gumroad.com/api) | 49 Amazon Selling Partner (developer-docs.amazon.com/sp-api) | 50 fanbasis (fanbasis.com)

**6. Data, SEO and Scraping**
51 DataForSEO (docs.dataforseo.com) | 52 SE Ranking (seranking.com/api) | 53 Ahrefs (ahrefs.com/api) | 54 MrScraper (docs.mrscraper.com) | 55 Apify (docs.apify.com) | 56 Firecrawl (firecrawl.dev) | 57 Bright Data (brightdata.com) | 58 Sherlock (github.com/sherlock-project/sherlock) | 59 Waterfall.io (waterfall.io, contact/company intel) | 60 Clay (clay.com)

**7. Developer, Infra and Data platforms**
61 GitHub (docs.github.com/rest) | 62 Vercel (vercel.com/docs/rest-api) | 63 Netlify (docs.netlify.com/api) | 64 Cloudflare (developers.cloudflare.com/api) | 65 Supabase (supabase.com/docs) | 66 Neo4j (neo4j.com/docs/api) | 67 Snowflake (docs.snowflake.com) | 68 MongoDB Atlas (mongodb.com/docs/atlas/api) | 69 Datadog (docs.datadoghq.com/api) | 70 Sentry (docs.sentry.io/api)

**8. Productivity and Project Management**
71 Notion (developers.notion.com) | 72 Airtable (airtable.com/developers) | 73 Linear (developers.linear.app) | 74 Jira (developer.atlassian.com) | 75 Asana (developers.asana.com) | 76 Monday.com (developer.monday.com) | 77 ClickUp (clickup.com/api) | 78 Coda (coda.io/developers) | 79 Smartsheet (smartsheet.com/developers) | 80 Harvest (harvestapp.com, help.getharvest.com/api-v2)

**9. Finance and Fintech**
81 Stripe (stripe.com/docs/api) | 82 Plaid (plaid.com/docs) | 83 Binance (binance-docs.github.io) | 84 Paygent Connect (paygent, NMI-powered) | 85 iPayX (ipayx.ai/docs) | 86 QuickBooks (developer.intuit.com) | 87 Xero (developer.xero.com) | 88 Brex (developer.brex.com) | 89 Ramp (docs.ramp.com) | 90 PitchBook (pitchbook.com, research API)

**10. AI, Research and Media-native**
91 NotebookLM (cloud.google.com/gemini, Enterprise API) | 92 Otter AI (help.otter.ai, MCP server) | 93 Fathom (fathom.video) | 94 Consensus (consensus.app, OAuth requested) | 95 Reducto (reducto.ai, document parsing) | 96 Devin (docs.devin.ai, MCP) | 97 higgsfield (higgsfield.ai/cli, content suite) | 98 Mermaid CLI (github.com/mermaid-js/mermaid-cli) | 99 YouTube Transcript (transcriptapi.com) | 100 Grain (grain.com, meeting notes)

Note: some apps are obscure or ambiguous (e.g. fanbasis, iPayX, Paygent Connect, Waterfall.io, Pumble, higgsfield). The hint is only a starting point. If the right product or docs cannot be identified confidently, mark `needs_human`. Do not invent.

## 10. Patterns (`patterns.py`)

Compute from `results.json` (exclude or separately count `needs_human`) and write `data/patterns.json`:
- Auth method distribution overall and per category (OAuth2 vs API key vs Basic vs other).
- Self-serve vs trial/paid vs gated, overall and per category.
- Most common blockers (count of `main_blocker`).
- Buildability counts overall and per category.
- **Easy wins**: apps with `buildability = yes` and `self_serve = self_serve` and (auth is OAuth2 or API key).
- **Needs outreach**: apps that are `gated` or `partner_gated` / `contact_sales`.
- Existing MCP coverage (how many already have official or community MCP).
- A few plain-English headline sentences generated from the numbers (write them in code from the counts, so they are always consistent with the data).

## 11. Web app (`app.py`, FastAPI + Jinja2)

Keep it plain. Minimal CSS (one small stylesheet or inline). No JS framework. A few lines of vanilla JS for table filters is fine.

- `GET /` : explainer page, in this order:
  1. **Headline patterns** (top, plain statements with numbers from `patterns.json`).
  2. **Easy wins vs needs outreach** (two short lists).
  3. **The agent**: what it does (diagram or numbered list of Steps A, B, C), tools used (Composio vs fallback usage counts), where a human was needed (the `needs_human` list with reasons).
  4. **Verification**: sample size, pass 1 vs pass 2 accuracy (overall and per field), full list of misses shown honestly.
  5. **Limitations and honesty notes** (apps that defeated the agent, known weaknesses).
  6. Link to `/results`, the repo, and how to run it.
- `GET /results` : the full 100-row table with columns: app, category, auth, self-serve, API type, MCP, buildability, blocker, evidence links, status. Filters: category, auth, self-serve, buildability, status. Rows with `needs_human` are visibly marked.
- `GET /results.json` : raw JSON of `results.json`.
- Also expose `GET /patterns.json` and `GET /verification.json` for machine consumption.

## 12. README.md (short)

- What this is (2 lines).
- Setup: `python -m venv`, `pip install -r requirements.txt`, copy `.env.example` to `.env` and fill the Composio key and the free LLM key.
- Run: `python agent.py --limit 5`, then `--all`, then `python patterns.py`, `python verify.py`, `uvicorn app:app --reload`.
- Where the human steps in (ground truth, needs_human apps).
- Live link and repo link placeholders.

## 13. Deploy

- Free host (Render or Railway). Start command: `uvicorn app:app --host 0.0.0.0 --port $PORT`.
- Commit the final `data/*.json` files so the deployed app serves the data without re-running the agent (the agent is run locally, results are committed).
- Do not commit `.env` or the raw cache.

## 14. Build order (do in this order, confirm each step works)

1. Scaffold repo, `requirements.txt`, `.env.example`, `apps.json` from section 9.
2. `schema.py` (record shape + validation).
3. `llm.py`: test with one trivial JSON prompt on the free LLM.
4. `tools.py`: test ONE Composio search call and ONE Composio fetch call (read the current Composio docs; report which toolkits worked on the free plan). Then add and test the fallback.
5. `agent.py` pass 1 on `--limit 3`; inspect output by eye; fix prompts. Then `--limit 10`.
6. `agent.py` pass 2; confirm it downgrades unsupported claims and flags `needs_human`.
7. Run `--all` (watch rate limits; resume from cache if interrupted).
8. `patterns.py`.
9. `verify.py`: generate sample + empty `ground_truth.json` template, then **stop and tell the user to fill the ground truth by hand from real docs**. After they finish, compute accuracy for pass 1 vs pass 2.
10. `app.py` + templates, run locally and check `/`, `/results`, `/results.json`.
11. README.
12. Deploy and confirm the live link works.

## 15. Things to tell the user as you go

- Which Composio toolkits worked on the free plan and which did not.
- How often the Composio path vs the fallback path was used.
- Any app you could not research and why.
- The exact pass 1 and pass 2 accuracy numbers, unrounded and unspun.

Do not claim anything works until you have actually run it.
