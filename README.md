# Job Radar — single consolidated repair (November 3, 2026)

**53 companies** retained: 42 configured on public JSON ATS feeds, 11 with no verified public JSON integration. This is not a guarantee that 42 feeds all return working jobs: a listed ATS token can move or its API can be temporarily unavailable. The workflow **will expose every source failure**. The complete list lives in `job_radar.py` under `SOURCES`, `NEEDS_ADAPTER`, `CUSTOM`.

## Replace existing files — do NOT reset the baseline

In your existing `agampreetkaurwalia-arch/Newonce` repository, replace the following files from this ZIP using GitHub's file editor or Git CLI:

- `job_radar.py`
- `.github/workflows/main.yml`
- `tests/test_job_radar.py`
- `requirements.txt`
- `README.md` (optional)

Do **not** delete or overwrite `data/state.json`: it was created by your previous successful polling and must survive the repair. Do not upload `job-radar-final/` as a nested directory; upload the **contents** of this folder in their matching paths. `.github/workflows/main.yml` must remain inside `.github/workflows/`.

Run **Actions → Job Radar Daily → Run workflow**. It will run daily at 09:34 UTC (15:04 IST); GitHub cron timing is best-effort. Standard hosted Actions are free for public repositories under GitHub's current terms, not a contractual perpetual guarantee.

## Confirmed ATS migrations and fixes

- Automattic: Greenhouse `automatticcareers`
- Appcues: Lever `appcues-2`
- Panopto: Lever `panopto`
- Kinsta: Lever `kinsta`
- Sporty Group: Greenhouse `sportygroup`
- Strike: Greenhouse `strike` (board observed, verify the live JSON response)
- Omniscient Digital: Ashby `omniscient`
- Float: Workable `floatjobs`
- Modash: Workable `modash`
- Zyte: Workable `zyte`
- SafetyWing: Pinpoint `safetywing` using the **documented** public `/postings.json` feed
- Prior corrections retained for Buffer, Chili Piper, Constructor, Contra, DuckDuckGo, Kit, TestGorilla, Toggl, Zapier (Ashby boards).

Sources used for discovering these mappings include the employers' public ATS-hosted jobs pages. Board URLs for corrections are recorded in `VERIFIED_BOARD_EVIDENCE`. These are **board evidence**, not promises that every JSON feed is currently accessible.

## JSON endpoints

| Platform | Anonymous JSON feed | Notes |
|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs` | `first_published` if provided = publication, `updated_at` is not creation |
| Lever | `https://api.lever.co/v0/postings/{token}?mode=json&limit=100&skip=0` | `createdAt` millisecond epoch, paginated |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{token}` | `publishedAt` is publication, not necessarily creation |
| Workable | `https://www.workable.com/api/accounts/{token}` | Vendor-documented anonymous endpoint; zero jobs can be suspect |
| Pinpoint | `https://{token}.pinpointhq.com/postings.json` | Vendor-documented anonymous posting JSON |

## Output and interpretation

- `output/report.md`: Markdown table plus source-by-source diagnostics.
- `output/jobs.csv`: eligible role titles, company, location, evidence type, posting date, direct apply URL.
- `output/status.json`: complete scan-health report for **all 53** companies.
- `data/state.json`: per-board set of job IDs from successful scans. It is automatically committed by the workflow.

**CONFIRMED_24H** means the job's **creation** timestamp is within UTC now minus 24 hours. **RECENT_VERIFY_PUBLICATION** means its publication timestamp is within that interval; publication might be a repost. **RECENT_VERIFY_FIRST_SEEN** means the ID first appeared between successful scans but its creation time is unknown. The first successful scan of a new ATS does **not** label every undated job newly posted. Old known published/created jobs are excluded even if a first-seen snapshot finds them. This is more conservative than treating `updated_at` as a creation date.

The scan never advances state when an API call fails, Lever pagination caps out, or Workable appears truncated. When a board that had jobs suddenly returns an empty feed, it retains the previous state until a second consecutive valid empty poll.

**Important limitation:** eleven companies do not currently expose a verified JSON integration in the package: Arkency, John Snow Labs, Livebird, Mailbird, MeetEdgar, ScaleMath, Scalerrs, Springworks, The Shelf, Tortuga, Pitch. Their presence in `status.json` means they are accounted for, **not** automatically monitored. Some have valid careers pages but no supported JSON feed. Springworks currently uses Zoho Recruit, which embeds JSON inside HTML instead of offering the requested stable public JSON endpoint. Unsupported source types are deliberately **not HTML-scraped**.

There is no general mechanism that can guarantee all 53 companies expose a stable, public, authentication-free API. A `200 OK` response also does not establish that a board is complete or correct. Inspect the run results and source diagnostics. If a company changes ATS, its mapping requires new public evidence.

## Local tests

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python job_radar.py
```

Tests are offline; a separate real run in GitHub Actions verifies the remote APIs. The updated workflow uses Node 24-capable actions, pins the Ubuntu version for less surprising runner migrations, and preserves reports even when sources fail. You should expect a red run if one or more configured feeds remains broken: **this is a transparency/safety feature, not evidence that job matching itself is broken.**
