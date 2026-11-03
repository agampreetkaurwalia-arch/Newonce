# Job Radar — 53 companies, public ATS JSON APIs

A daily no-HTML job monitor for 40 configured ATS boards and 13 explicitly unverified custom sources. All names and supplied tokens are included. **Tokens are assumptions, not guaranteed valid endpoints**. An `ERROR` status means the board must be validated or updated. API JSON is not intrinsically permanent, so monitoring is essential.

## Setup with GitHub web UI

1. Create a new **public** repository (public standard GitHub-hosted runners are free within GitHub's published policies).
2. Upload the files from this directory, including `.github/workflows/main.yml`. Keep the same paths. Make the repository's default branch `main` (or leave its existing default branch as-is).
3. Open `Settings → Actions → General` and set **Workflow permissions** to **Read and write permissions**, if offered. Branch protection and organization policies may prohibit the bot's state commits.
4. Open `Actions → Job Radar Daily → Run workflow` to run once manually. Wait for the Actions job to finish and inspect the artifact `job-radar-N` and repository `output/report.md`.
5. Verify the **Source diagnostics** section. `NEEDS_VERIFIED_PUBLIC_API` is intentional for 13 custom companies. Other `ERROR`, `PARTIAL`, or `EMPTY` statuses require inspection; they are not proof of no openings.
6. The schedule is `34 9 * * *`: every day at **09:34 UTC**, **15:04 India Standard Time**. Scheduled workflows run from the default branch; GitHub may delay or occasionally skip scheduled runs.
7. Change the comma-separated `ROLE_KEYWORDS` in `.github/workflows/main.yml`, then commit. Checking is case-insensitive and looks at the **title**, not the job description. Default: Software Engineer, Product Manager, Designer.

## What files you get

- `output/report.md`: markdown job table plus complete status table
- `output/jobs.csv`: matched new/verify jobs (empty CSV on no matches)
- `output/status.json`: machine-readable source status and execution window
- `data/state.json`: committed snapshot of previously seen IDs (created automatically)

The first successful fetch of a source creates its baseline. Timestamp-confirmed new jobs can be reported on that first scan. Older, undated roles are not backfilled as new. On subsequent successful scans, newly observed undated job IDs are marked `RECENT_VERIFY_FIRST_SEEN`, **not confirmed posted within 24 hours**. Ashby `publishedAt` is publication time and may reflect republication. Greenhouse `updated_at` never proves creation date. An old reliable creation date is excluded.

Failed/possibly truncated sources **do not overwrite** previously saved source state. The scan runs with up to five concurrent sources, retries transient HTTP errors, paginates Lever, and raises a GitHub Actions failure for an API source failure or potentially truncated feed. The reports and healthy snapshots are still persisted before marking the run red.

## Local tests and run

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python job_radar.py
```

Run twice to verify first-seen suppression. Use `ROLE_KEYWORDS='Product Manager,Designer' python job_radar.py` to override default roles on macOS/Linux, or set the variable appropriately on Windows.

## Limitations and necessary maintenance

- **53 companies**, not 51: 17 Greenhouse + 14 Lever + 6 Ashby + 3 Workable + 13 custom.
- All tokens were supplied by the requester and are not individually verified against live active company career pages. A valid-looking token may be outdated, a company may have migrated ATS, and an HTTP 200 response may represent a different employer.
- The 13 custom sources are **not scanned** and are visibly flagged. No guessed endpoints or fragile HTML fallbacks.
- Workable's public API can have limited output or rate-limit; feeds with 30+ jobs are conservatively flagged as possibly truncated, requiring verification.
- Requiring an exact 24-hour creation date means undated, legitimate new postings appear in Verify rather than Confirmed. No system can reconstruct a missing historical creation timestamp with certainty.
- The workflow is designed to be free for a public repository under GitHub's standard free public-runner offering; private-repository quotas, GitHub policy changes, API rate limits, or third-party services can affect cost/availability. No deployment can honestly promise 100% uptime or zero cost forever.
- Schedule is best-effort, not precise-to-the-minute. To avoid missed slots, use a more frequent schedule if exact completeness matters. A 24-hour window with only daily runs can miss postings if the runner is delayed beyond the prior day.
- No email is sent. See GitHub Actions artifacts, the committed report, or GitHub Actions failure notifications.
