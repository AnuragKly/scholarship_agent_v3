# Scholarship Research Agent — Version 3

A personal, AI-assisted tool that helps a Nepali undergraduate find and track international **Master's scholarships**
in Applied Mathematics, Computational Mathematics and Data Science. It reads the **official web page** of each
scholarship, extracts the facts with evidence, checks eligibility criterion by criterion, and never claims anything the
source does not say.

# How to use it

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

## Feature checklist

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

---a

## 6. Known limits

* A scholarship page can be wrong, outdated or ambiguous; the app shows what it says, not the truth. **Always confirm on
  the official page before applying.** "CONFIRMED" means "the source text states it", never an official decision.
* Subject compatibility is keyword-based and says nothing about admission to a specific programme.
* GPA scales and grade conversions differ between countries; the app compares numbers only when both scales are stated.
