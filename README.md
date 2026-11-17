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

## Known limits

* A scholarship page can be wrong, outdated or ambiguous; the app shows what it says, not the truth. **Always confirm on
  the official page before applying.** "CONFIRMED" means "the source text states it", never an official decision.
* Subject compatibility is keyword-based and says nothing about admission to a specific programme.
* GPA scales and grade conversions differ between countries; the app compares numbers only when both scales are stated.
