#!/usr/bin/env python3
"""Public ATS job monitor. Python 3.12+, requests only.

Uses source-specific normalization, strict UTC age filtering, and a committed
snapshot for first-seen detection. Never claims an updated job was newly created.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger('job_radar')
ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / 'data' / 'state.json'
OUT_DIR = ROOT / 'output'
KEYWORDS = [x.strip().casefold() for x in os.getenv(
    'ROLE_KEYWORDS', 'Product Manager,Program Manager,Project Manager,Chief of Staff,Strategy,Strategic,Consultant,Consulting,Founder,Operations Manager,Business Operations,Partnerships,Software Engineer,Designer'
).split(',') if x.strip()]
TIMEOUT = (8, 22)
MAX_PAGES = 50
PAGE_SIZE = 100

# New/migrated board URLs established from public company pages, 2026-11-03.
# Some original mappings remain speculative until a live API scan succeeds.
# Do not conflate a verified board URL with proof that its JSON endpoint responds.
SOURCES = {
    'greenhouse': {
        'Alpaca':'alpaca', 'Brave':'brave',
        'Automattic':'automatticcareers', 'GitLab':'gitlab',
        'Remote':'remote',
        'Shogun':'shogun', 'YNAB':'youneedabudget',
        'Fingerprint':'fingerprint', 'Sporty Group':'sportygroup', 'Strike':'strike',
    },
    'lever': {
        'Doist':'doist', 'Groove':'groovehq', 'Appcues':'appcues-2', 'Panopto':'panopto', 'Kinsta':'kinsta',
        'Liveblocks':'liveblocks', 'Podia':'podia',
        'Storylane':'storylane', 'tl;dv':'tldv',
        'Whereby':'whereby',
    },
    'ashby': {
        'Supabase':'supabase', 'Ghost':'ghost',
        'MailerLite':'mailerlite', 'Plus':'plus', 'Smile':'smile',
        'Buffer':'buffer', 'Chili Piper':'chilipiper',
        'Constructor':'constructor', 'Contra':'contra',
        'DuckDuckGo':'duck-duck-go', 'Kit':'kit',
        'TestGorilla':'testgorilla', 'Toggl':'toggl', 'Zapier':'zapier', 'Omniscient Digital':'omniscient',
    },
    'pinpoint': {'SafetyWing':'safetywing'},
    'workable': {
        'Awesome Motive':'awesome-motive', 'Namecheap':'namecheap',
        'SimpleTexting':'simpletexting', 'Modash':'modash', 'Zyte':'zyte', 'Float':'floatjobs',
    },
}
# These companies have no confirmed public JSON feed. Never fake an empty board.
NEEDS_ADAPTER = {
    'Pitch': 'Company-hosted careers page (pitch.com/jobs); API not verified',
}
# Evidence of board tokens, NOT certification that the corresponding API is live.
VERIFIED_BOARD_EVIDENCE = {
    'Automattic':'https://job-boards.greenhouse.io/automatticcareers',
    'Appcues':'https://jobs.lever.co/appcues-2',
    'Panopto':'https://jobs.lever.co/panopto',
    'Kinsta':'https://jobs.lever.co/kinsta',
    'Sporty Group':'https://job-boards.greenhouse.io/sportygroup',
    'Strike':'https://job-boards.greenhouse.io/strike',
    'Omniscient Digital':'https://jobs.ashbyhq.com/omniscient',
    'Float':'https://apply.workable.com/floatjobs',
    'Modash':'https://apply.workable.com/modash',
    'Zyte':'https://apply.workable.com/zyte',
    'Buffer':'https://jobs.ashbyhq.com/buffer/6ee07995-2738-4cee-b16d-fc8967674346/',
    'Chili Piper':'https://jobs.ashbyhq.com/chilipiper/9caf9cb4-b51d-4f20-b269-dadca1c1f2a9',
    'Constructor':'https://jobs.ashbyhq.com/constructor/4faaf6fd-272f-41ed-85e9-960b7d610a0b',
    'Contra':'https://jobs.ashbyhq.com/contra/55b378db-0732-40c0-8a68-a11569978080/',
    'DuckDuckGo':'https://jobs.ashbyhq.com/duck-duck-go/d02d7598-141b-4a84-bdf6-46677da89b08',
    'Kit':'https://jobs.ashbyhq.com/kit/e892fb67-74bb-4de4-9827-b983e203a005',
    'TestGorilla':'https://jobs.ashbyhq.com/testgorilla/c0ac7ba5-c016-44b2-b19e-c5b2a9cd85b9',
    'Toggl':'https://jobs.ashbyhq.com/toggl/a4f3e1f5-753b-4af9-b068-86a09a164cd2',
    'Zapier':'https://jobs.ashbyhq.com/zapier/38434b88-086c-424b-8d18-8d006e0b71b8',
}

CUSTOM = [
    'Arkency','John Snow Labs','Livebird','Mailbird','MeetEdgar',
    'ScaleMath','Scalerrs',
    'Springworks','The Shelf','Tortuga',
]

@dataclass(frozen=True)
class Job:
    company: str
    platform: str
    job_id: str
    title: str
    location: str
    url: str
    created_at: str | None = None
    published_at: str | None = None
    updated_at: str | None = None

    @property
    def key(self) -> str:
        return f'{self.platform}:{self.company}:{self.job_id}'


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_date(value) -> datetime | None:
    if value in (None, '') or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (float, int)):
            # Lever's epoch-millisecond timestamps; also accept seconds.
            return datetime.fromtimestamp(value / (1000 if abs(value) > 10**11 else 1), tz=timezone.utc)
        raw = str(value).strip()
        if re.fullmatch(r'\d{10,13}', raw):
            return parse_date(int(raw))
        # A date without a clock cannot certify an exact 24-hour interval.
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', raw):
            return None
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (ValueError, OverflowError, OSError):
        return None


def clean(value, default='Unknown') -> str:
    s = re.sub(r'\s+', ' ', str(value or '')).strip()
    return s or default


def direct_url(value) -> str:
    url = clean(value, '')
    parsed = urlparse(url)
    if parsed.scheme not in ('https', 'http') or not parsed.netloc:
        return ''
    return url


def session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, connect=3, read=2, backoff_factor=1, backoff_max=8,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(['GET']),
                  respect_retry_after_header=False)
    s.mount('https://', HTTPAdapter(max_retries=retry))
    s.headers.update({'Accept':'application/json',
                      'User-Agent':'JobRadar/1.0 (public ATS feeds; daily)'})
    return s


def fetch_json(s: requests.Session, url: str, params=None):
    response = s.get(url, params=params, timeout=TIMEOUT)
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise ValueError('Response was not JSON') from exc


def normalize(platform: str, company: str, item: dict) -> Job | None:
    if not isinstance(item, dict):
        return None
    if platform == 'greenhouse':
        identifier = item.get('id')
        title = item.get('title')
        location = (item.get('location') or {}).get('name') if isinstance(item.get('location'), dict) else item.get('location')
        link = item.get('absolute_url')
        created, published, updated = None, item.get('first_published'), item.get('updated_at')
    elif platform == 'lever':
        identifier = item.get('id')
        title = item.get('text')
        cats = item.get('categories') or {}
        location = cats.get('location') or item.get('workplaceType')
        link = item.get('applyUrl') or item.get('hostedUrl')
        created, published, updated = item.get('createdAt'), None, item.get('updatedAt')
    elif platform == 'ashby':
        if item.get('isListed') is False:
            return None
        identifier = item.get('id') or item.get('jobUrl')
        title = item.get('title')
        secondary = [x.get('location') for x in (item.get('secondaryLocations') or []) if isinstance(x, dict)]
        location = ', '.join(dict.fromkeys(str(x) for x in [item.get('location'), *secondary] if x))
        if not location and item.get('isRemote'):
            location = 'Remote (region unspecified)'
        link = item.get('applyUrl') or item.get('jobUrl')
        created, published, updated = None, item.get('publishedAt'), None
    elif platform == 'pinpoint':
        identifier = item.get('id')
        title = item.get('title')
        location_field = item.get('location') or {}
        location = location_field.get('name') if isinstance(location_field, dict) else location_field
        if item.get('workplace_type') == 'remote':
            location = str(location or 'Region unspecified') + ' (Remote)'
        link = item.get('url')
        created, published, updated = None, None, None
    elif platform == 'workable':
        identifier = item.get('shortcode') or item.get('code') or item.get('id')
        title = item.get('title')
        loc = item.get('location')
        if isinstance(loc, dict):
            location = loc.get('location_str') or ', '.join(str(loc.get(k)) for k in ('city','country') if loc.get(k))
        else:
            location = ', '.join(str(item[k]) for k in ('city','state','country') if item.get(k))
        if item.get('telecommuting') and not location:
            location = 'Remote (region unspecified)'
        # In Workable's public schema `url` is an application form; application_url may be a detail page.
        link = item.get('url') or item.get('shortlink') or item.get('application_url')
        created, published, updated = item.get('created_at'), item.get('published_on'), item.get('updated_at')
    else:
        raise ValueError(f'Unsupported platform: {platform}')
    url = direct_url(link)
    if not identifier or not title or not url:
        return None
    def iso(x):
        dt = parse_date(x)
        return dt.isoformat() if dt else None
    return Job(company, platform, str(identifier), clean(title), clean(location), url,
               iso(created), iso(published), iso(updated))


def fetch_company(platform: str, company: str, token: str) -> tuple[list[Job], str]:
    with session() as s:
        if platform == 'greenhouse':
            data = fetch_json(s, f'https://boards-api.greenhouse.io/v1/boards/{token}/jobs')
            if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
                raise ValueError('Unexpected Greenhouse JSON schema')
            raw = data['jobs']
        elif platform == 'ashby':
            data = fetch_json(s, f'https://api.ashbyhq.com/posting-api/job-board/{token}')
            if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
                raise ValueError('Unexpected Ashby JSON schema')
            raw = data['jobs']
        elif platform == 'pinpoint':
            data = fetch_json(s, f'https://{token}.pinpointhq.com/postings.json')
            if not isinstance(data, dict) or not isinstance(data.get('data'), list):
                raise ValueError('Unexpected Pinpoint JSON schema')
            raw = data['data']
        elif platform == 'workable':
            data = fetch_json(s, f'https://www.workable.com/api/accounts/{token}')
            if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
                raise ValueError('Unexpected Workable JSON schema')
            raw = data['jobs']
            # Some public Workable variants cap the result count: don't claim completeness.
            if len(raw) >= 30:
                note = 'POSSIBLY_TRUNCATED_WORKABLE_FEED'
            else:
                note = 'EMPTY_VERIFY' if len(raw) == 0 else 'OK'
        elif platform == 'lever':
            raw = []
            note = 'OK'
            for page in range(MAX_PAGES):
                batch = fetch_json(s, f'https://api.lever.co/v0/postings/{token}',
                                   {'mode':'json', 'skip':page * PAGE_SIZE, 'limit':PAGE_SIZE})
                if not isinstance(batch, list):
                    raise ValueError('Unexpected Lever JSON schema')
                raw.extend(batch)
                if len(batch) < PAGE_SIZE:
                    break
            else:
                note = 'PAGINATION_LIMIT_REACHED'
        else:
            raise ValueError(f'Unsupported platform {platform}')
    if platform not in ('lever', 'workable'):
        note = 'EMPTY_VERIFY' if len(raw) == 0 else 'OK'
    jobs = [j for item in raw if (j := normalize(platform, company, item))]
    skipped = len(raw) - len(jobs)
    if raw and not jobs:
        if platform == 'ashby' and all(isinstance(x, dict) and x.get('isListed') is False for x in raw):
            note = 'EMPTY_VERIFY_ALL_UNLISTED'
        else:
            raise ValueError(f'All {len(raw)} returned rows were unusable; verify schema')
    if skipped:
        note += f'; {skipped} INVALID_OR_UNLISTED_ROWS'
    return jobs, note


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {'version':1, 'sources':{}}
    obj = json.loads(STATE_FILE.read_text(encoding='utf-8'))
    if not isinstance(obj, dict) or obj.get('version') != 1 or not isinstance(obj.get('sources'), dict):
        raise ValueError('Invalid persistent state (do not silently reset)')
    return obj


def classify(job: Job, previous_ids: set[str], has_baseline: bool, start: datetime, now: datetime) -> str | None:
    if job.key in previous_ids:
        return None
    created = parse_date(job.created_at)
    if created:
        return 'CONFIRMED_24H' if start <= created <= now else None
    published = parse_date(job.published_at)
    if published:
        if start <= published <= now:
            return 'RECENT_VERIFY_PUBLICATION'
        # A known old publication date overrides first-seen ambiguity.
        return None
    if has_baseline:
        return 'RECENT_VERIFY_FIRST_SEEN'
    return None  # The first scan is a baseline, not a sudden influx of new jobs.


def md_escape(value: str) -> str:
    return value.replace('|', '\\|').replace('\n', ' ')


def save_reports(rows: list[dict], statuses: list[dict], now: datetime, start: datetime) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows.sort(key=lambda x: (x['status'] != 'CONFIRMED_24H', x['company'].casefold(), x['title'].casefold()))
    with (OUT_DIR / 'jobs.csv').open('w', newline='', encoding='utf-8') as f:
        fields = ['company','title','location','status','date_utc','url']
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    (OUT_DIR / 'status.json').write_text(json.dumps({'started_utc':now.isoformat(),
        'cutoff_utc':start.isoformat(), 'sources':statuses}, indent=2) + '\n', encoding='utf-8')
    lines = [
        '# Job Radar — past 24 hours', '',
        f'Execution UTC: {now.isoformat()}', f'Window starts UTC: {start.isoformat()}',
        f'Keywords: {", ".join(KEYWORDS) or "ALL"}', '',
        f'Confirmed new: {sum(r["status"] == "CONFIRMED_24H" for r in rows)}  ',
        f'Recent / Verify: {sum(r["status"] != "CONFIRMED_24H" for r in rows)}', '',
        '| Company | Job title | Location | Evidence | Date (UTC) | Apply |',
        '|---|---|---|---|---|---|',
    ]
    for r in rows:
        cells = [md_escape(r[k]) for k in ('company','title','location','status','date_utc')]
        lines.append('| ' + ' | '.join(cells) + f' | [Apply]({r["url"]}) |')
    if not rows:
        lines.append('| — | No new matched jobs found on successfully scanned sources | — | — | — | — |')
    lines += ['', '## Source diagnostics', '', '| Company | ATS | Fetched | Status |', '|---|---|---:|---|']
    for s in statuses:
        lines.append(f'| {md_escape(s["company"])} | {s["platform"]} | {s["count"]} | {md_escape(s["status"])} |')
    lines += ['', 'EMPTY_VERIFY means a valid-looking but empty feed that needs independent validation. ', 'Interpretation: CONFIRMED_24H uses an actual creation timestamp. '
              'RECENT_VERIFY_PUBLICATION means a publication timestamp, not necessarily initial creation. '
              'RECENT_VERIFY_FIRST_SEEN means absent from the preceding successful snapshot, '
              'not proven posted within 24 hours. The first successful source scan establishes its baseline.', '']
    (OUT_DIR / 'report.md').write_text('\n'.join(lines), encoding='utf-8')
    print('\n'.join(lines))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    if not KEYWORDS:
        LOG.warning('ROLE_KEYWORDS is empty: all titles will be matched')
    now = utc_now()
    start = now - timedelta(hours=24)
    try:
        state = load_state()
    except (OSError, ValueError) as exc:
        LOG.error('Could not load state: %s', exc)
        return 2
    results: list[dict] = []
    statuses: list[dict] = []
    tasks = [(platform, company, token) for platform, boards in SOURCES.items() for company, token in boards.items()]
    updates = {}
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {executor.submit(fetch_company, *task):task for task in tasks}
        for future in as_completed(futures):
            platform, company, token = futures[future]
            source_key = f'{platform}:{company}'
            try:
                jobs, note = future.result()
                prev = state['sources'].get(source_key)
                known_ids = set(prev['ids']) if isinstance(prev, dict) and isinstance(prev.get('ids'), list) else set()
                has_baseline = isinstance(prev, dict) and isinstance(prev.get('ids'), list)
                # If ATS changed, treat the first scan on the new platform as a
                # baseline for undated jobs (don't call every migrated job new).
                # Publication timestamps in the actual window remain eligible.
                migrated = not has_baseline and any(
                    k.endswith(':' + company) for k in state['sources'])
                matched = 0
                for job in jobs:
                    if KEYWORDS and not any(k in job.title.casefold() for k in KEYWORDS):
                        continue
                    matched += 1
                    flag = classify(job, known_ids, has_baseline and not migrated, start, now)
                    if flag:
                        results.append({'company':job.company, 'title':job.title, 'location':job.location,
                                        'status':flag, 'date_utc':job.created_at or job.published_at or 'Unknown',
                                        'url':job.url})
                # Do not advance baseline when a source may be truncated.
                safe = not note.startswith(('POSSIBLY_TRUNCATED', 'PAGINATION_LIMIT'))
                # Empty responses, especially on a platform migration, need confirmation.
                # Never overwrite a previously nonempty snapshot with an empty feed.
                if not jobs and known_ids:
                    # A valid empty API response may be temporary. Require two
                    # consecutive successful empty polls before clearing the set.
                    empty_streak = int(prev.get('empty_streak', 0)) + 1 if isinstance(prev, dict) else 1
                    if empty_streak < 2:
                        safe = False
                        note = 'EMPTY_AFTER_NONEMPTY_VERIFY; retained previous snapshot (1/2)'
                        updates[source_key] = {**prev, 'empty_streak': empty_streak}
                    else:
                        note = 'EMPTY_CONFIRMED_TWO_POLLS'
                else:
                    empty_streak = 0
                if safe:
                    updates[source_key] = {'ids':sorted({job.key for job in jobs}), 'last_success_utc':now.isoformat(), 'empty_streak':0}
                statuses.append({'company':company,'platform':platform,'count':len(jobs),
                                 'matched_titles':matched,'status':note if safe else 'PARTIAL: '+note})
                LOG.info('%s [%s]: %d jobs, %d matching titles, %s', company, platform, len(jobs), matched, note)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                statuses.append({'company':company,'platform':platform,'count':0,
                                 'matched_titles':0,'status':f'ERROR: {str(exc)[:220]}'})
                LOG.warning('%s [%s]: %s', company, platform, exc)
    for company, explanation in NEEDS_ADAPTER.items():
        statuses.append({'company':company,'platform':'custom','count':0,
                         'matched_titles':0,'status':'NEEDS_ADAPTER: '+explanation})
    for company in CUSTOM:
        statuses.append({'company':company,'platform':'custom','count':0,
                         'matched_titles':0,'status':'NEEDS_VERIFIED_PUBLIC_API'})
    statuses.sort(key=lambda x:x['company'].casefold())
    save_reports(results, statuses, now, start)
    state['sources'].update(updates)
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = STATE_FILE.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    temp.replace(STATE_FILE)
    errors = sum(s['status'].startswith(('ERROR:', 'PARTIAL:')) for s in statuses)
    LOG.info('Sources: %d; API failures/partial: %d; custom unverified: %d; alerts: %d',
             len(statuses), errors, len(CUSTOM), len(results))
    # Nonzero for API failure prevents a false green CI run. State/report are still persisted.
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
