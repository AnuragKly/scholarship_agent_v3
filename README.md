# Scholarship Research Agent — Version 3

A personal, AI-assisted tool that helps a Nepali undergraduate find and track international **Master's scholarships**
in Applied Mathematics, Computational Mathematics and Data Science. It reads the **official web page** of each
scholarship, extracts the facts with evidence, checks eligibility criterion by criterion, and never claims anything the
source does not say.

> **Built from scratch.** The Version 2 project was not available when this was written, so this is a fresh build to the
> same specification rather than an upgrade. See "Version 2 data" below.

---

## 1. Setup on macOS (beginner guide)

You need **Python 3.10 or newer**. Check in Terminal: `python3 --version`. If it is older or missing, install it from
<https://www.python.org/downloads/>.

### Easiest way

1. Unzip the project and open the folder.
2. Double-click **`run_app.command`** (the first time: right-click → *Open*).
3. It creates a private Python environment, installs everything, and creates a `.env` file for you.
4. Add your keys to `.env` (step 3 below), save, and double-click `run_app.command` again.
5. Your browser opens the app (or go to <http://localhost:8501>).

### Manual way (Terminal)

```bash
cd path/to/scholarship_agent_v3
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
open -e .env            # edit the keys, save
streamlit run app.py
```

### 3. Add your API keys (both optional)

Edit `.env`:

| Key | What it does | Without it |
|---|---|---|
| `OPENAI_API_KEY` | AI extraction of scholarship pages | A limited heuristic fallback is used and **clearly labelled "NOT AI-verified"** |
| `OPENAI_MODEL` | Model name (default `gpt-4o-mini`; must support Structured Outputs) | — |
| `TAVILY_API_KEY` | Search on the Discover page | Search is disabled; you can still paste an official URL |

**Costs:** OpenAI and Tavily are third-party services. Usage may incur **separate charges on your own accounts**. The
app never shows or logs your keys, and `.env` is already in `.gitignore`.

### Run the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

No test makes a live or paid API call; OpenAI, Tavily and the web are all mocked.

### Troubleshooting

| Problem | Fix |
|---|---|
| `command not found: python3` | Install Python from python.org |
| `ModuleNotFoundError` | Activate the environment (`source .venv/bin/activate`) and run `pip install -r requirements.txt` |
| Dashboard says a key is "not set" | Check the file is named exactly `.env` (not `.env.txt`), is in the project folder, and you restarted the app |
| "OpenAI rejected the API key" | Re-copy the key; check billing at platform.openai.com |
| "OpenAI rejected the request… model" | Set `OPENAI_MODEL` to a model that supports Structured Outputs (e.g. `gpt-4o-mini`) |
| "Search rate limit or quota reached" | Wait, or check your Tavily plan |
| "Very little readable text was found" | The page needs JavaScript. Open it in your browser, copy the text, use **Paste page text** on Discover |
| "The website refused automated access" | Same: use **Paste page text** |
| "The app could not start" | Make sure the `data` folder is writable; set `SCHOLARSHIP_DB_PATH` in `.env` to another location |
| Port already in use | `streamlit run app.py --server.port 8502` |

---

## 2. How to use it

1. **Settings** — enter your profile (nationality, subjects, funding preference; GPA, English score, experience and age
   are optional). Empty means *unknown*: the app never assumes a value for you.
2. **Discover** — search, or paste an official page address. Click **Research This Page**. The app opens the page,
   extracts the facts, and shows the summary, evidence, missing information, eligibility matrix and match score.
3. Click **Add Opportunity** to save the complete record (nothing is saved before that). You get the saved ID.
   If it looks like something you already saved, the app offers **Update existing record** (history is kept) instead
   of creating a copy or overwriting.
4. **Saved Opportunities** — filter, sort, export CSV, open the full detail view.
5. **Review & Verify** — compare with the official page, record your own decision on any criterion, mark verified.
6. **Change History** — re-check saved sources; differences are stored for your review and are **never applied
   automatically**.

### Rules the app follows

* **Official pages only as evidence.** Search snippets are never treated as facts. Third-party sites are flagged.
* **Nepal eligibility** is "explicitly eligible" only if the page names Nepal *and* a quotation proving it is found
  word-for-word on the page. "International students" never counts.
* **Overall eligibility is never CONFIRMED** while any mandatory criterion is unclear — including your GPA/English
  score if you did not enter them.
* **Match score ≠ eligibility.** The score only ranks relevance; missing data earns zero points.
* **Deadlines:** missing → *Unknown* (never "open"); past → *Passed*; no timezone given → the earliest possible
  reading is used; conflicting dates are flagged. Shown in Asia/Kathmandu.
* **Privacy:** only the web page text is sent to OpenAI. Your profile (GPA, scores, age…) stays on your computer.
  Search queries sent to Tavily contain subjects, countries and nationality wording.

---

## 3. Module guide

| Module | Purpose |
|---|---|
| `app.py` | Streamlit entry point: navigation only |
| `scholarship_agent/config.py` | Reads `.env`/environment, validates, never exposes secrets |
| `utils.py` | Secret-redacting logging, URL normalisation, JSON/CSV helpers (CSV formula-injection safe) |
| `record.py` | The one record definition + strict JSON schema + repair of malformed AI output |
| `extraction.py` | OpenAI Responses API + Structured Outputs, prompt-injection rules, guard rails, labelled fallback, multi-source merge with conflicts |
| `fetcher.py` | Safe HTTP fetching (scheme/private-IP/redirect/size/type checks), readable text, canonical URL, candidate links |
| `discovery.py` | Query building (always targets Nepal applicants) + Tavily search |
| `sources.py` | Official / third-party classification (heuristic, overridable) |
| `eligibility.py` | CONFIRMED / NOT_MET / UNCLEAR / NOT_APPLICABLE per criterion, with evidence |
| `subjects.py` | Subject keyword matching ("likely compatibility", not admission) |
| `matching.py` | Transparent 0–100 score with configurable weights |
| `deadlines.py` | Date normalisation, status (Open / Closing soon / Passed / Unknown), Nepal time |
| `dedup.py` | Duplicate detection: normalised URL, then name/provider similarity |
| `changes.py` | Field-level comparison of two records |
| `migrations.py`, `database.py` | Versioned, transactional SQLite migrations (with backup) and connections |
| `repository.py` | All parameterised SQL: opportunities, snapshots, change log, discoveries |
| `settings.py` | Validated applicant profile and weights stored in SQLite |
| `filters.py` | Combinable filters (unknown = own category), sorting, summary cards, CSV |
| `pipeline.py` | Orchestration: fetch → extract → assess → score → save; source re-checks |
| `ui/` | One file per page + shared components |
| `scripts/migrate_db.py` | Stand-alone migration (also runs automatically on startup) |

---

## 4. Version 2 data

The Version 2 project and database were never provided, so **nothing could be tested against a real Version 2 file**.
What exists: on startup (or via `python scripts/migrate_db.py old.db`) the migration backs up the file, renames any
clashing old table to `legacy_<name>_v2` (never dropped) and imports rows by *commonly named* columns (title/name,
university/provider, country, url, deadline, notes). Imported rows are marked **"Imported (V2)"**, unverified, with
eligibility unclear. This is tested only against a *simulated* legacy table. If your real V2 file uses other column names,
your original data is still safe in the `legacy_*_v2` table and the import can be adjusted.

---

## 5. Feature checklist

**Legend:** ✅ implemented and covered by automated tests · 🟡 partly implemented / tested only in part · ❌ not implemented

**Important:** the OpenAI and Tavily integrations have **never been run against the live services** (no keys were
available and live paid calls were out of scope). They are tested against mocks that follow the documented request/response
shape. Expect to check the first real run yourself.

| # | Area | Status | Notes |
|---|---|---|---|
| 1 | Modular architecture, docstrings, type hints, logging, config validation | ✅ | `app.py` is navigation only |
| 2 | AI extraction (Responses API, strict schema, injection rules, fallback labelled) | 🟡 | Logic ✅ with mocks; **not run live** |
| 2 | Evidence quotes verified against page text | ✅ | Unfound quotes are flagged |
| 3 | Webpage fetching: safety limits, redirects, errors, manual URL, linked pages | ✅ | |
| 3 | JavaScript-rendered pages | ❌ | Reported as unreadable; use **Paste page text** (no headless browser, by your choice) |
| 3 | PDF pages | ❌ | Refused with a clear message; paste text instead |
| 4 | Source type/domain recorded, third-party flagged | 🟡 | Domain-based heuristic; correctable in Review & Verify |
| 4 | Disagreeing sources kept side by side | 🟡 | Scalar fields only; the app flags which dates differ but does **not** decide which applies to your programme |
| 5 | Eligibility module, four states, overall rule, evidence | ✅ | GPA/language/age/experience parsing is pattern-based; unusual wording becomes UNCLEAR, never a guess |
| 6 | SQLite, migrations, transactions, parameterised SQL | ✅ | |
| 6 | Duplicate prevention + update-existing workflow | ✅ | Add/duplicate-offer clicks UI-tested; the *Update* click is logic-tested only |
| 6 | Preserving Version 2 records | 🟡 | See section 4 — simulated only |
| 7 | Discover workflow, new vs saved leads, Add Opportunity with ID | 🟡 | Add click UI-tested; **search not tested live** |
| 8 | Filters, dashboard cards, sorting, CSV export | ✅ | Logic tested; widgets render (not click-tested individually) |
| 9 | Detail view with all fields | ✅ | Renders (tested); wording check by eye only |
| 10 | Settings in UI, persistent, validated, reset | 🟡 | Logic ✅; form buttons not click-tested |
| 11 | Transparent configurable match score | ✅ | |
| 12 | Deadline tracking in Nepal time | ✅ | |
| 13 | Change tracking and history | 🟡 | Field-level comparison for the primary page (needs OpenAI key); linked pages by content fingerprint only; no automatic scheduled checks (manual button) |
| 14 | Six-page responsive UI | 🟡 | Renders in desktop Chromium; **not checked on a phone-size screen** |
| 15 | Security/privacy (keys in `.env`, redaction, URL validation, safe SQL) | ✅ | |
| 16 | Tests, runs with and without keys | ✅ | 146 tests, all mocked |
| 17 | ZIP, README, `.env.example`, `.gitignore`, migration script | ✅ | |
| — | Scheduled/automatic re-checks, e-mail alerts, multi-user login | ❌ | Not requested |

---

## 6. Known limits

* A scholarship page can be wrong, outdated or ambiguous; the app shows what it says, not the truth. **Always confirm on
  the official page before applying.** "CONFIRMED" means "the source text states it", never an official decision.
* Subject compatibility is keyword-based and says nothing about admission to a specific programme.
* GPA scales and grade conversions differ between countries; the app compares numbers only when both scales are stated.
