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
    'ROLE_KEYWORDS', 'Software Engineer,Product Manager,Designer'
).split(',') if x.strip()]
TIMEOUT = (8, 22)
MAX_PAGES = 50
PAGE_SIZE = 100

SOURCES = {
    'greenhouse': {
        'Alpaca':'alpaca', 'Brave':'brave', 'Appcues':'appcues',
        'Automattic':'automattic','Chili Piper':'chilipiper',
        'Constructor':'constructor','DuckDuckGo':'duckduckgo',
        'GitLab':'gitlab','Panopto':'panopto','Pitch':'pitch',
        'Remote':'remote','SafetyWing':'safetywing','Shogun':'shogun',
        'TestGorilla':'testgorilla','YNAB':'youneedabudget',
        'Zapier':'zapier','Fingerprint':'fingerprint',
    },
    'lever': {
        'Buffer':'buffer','Contra':'contra','Doist':'doist',
        'Float':'float','Groove':'groovehq','Kit':'kit-2',
        'Liveblocks':'liveblocks','Modash':'modash','Podia':'podia',
        'Storylane':'storylane','tl;dv':'tldv','Toggl':'toggl',
        'Whereby':'whereby','Zyte':'zyte',
    },
    'ashby': {
        'Supabase':'supabase','Ghost':'ghost','Kinsta':'kinsta',
        'MailerLite':'mailerlite','Plus':'plus','Smile':'smile',
    },
    'workable': {
        'Awesome Motive':'awesome-motive','Namecheap':'namecheap',
        'SimpleTexting':'simpletexting',
    },
}
CUSTOM = [
    'Arkency','John Snow Labs','Livebird','Mailbird','MeetEdgar',
    'Omniscient Digital','ScaleMath','Scalerrs','Sporty Group',
    'Springworks','The Shelf','Tortuga','Strike',
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
        created, published, updated = None, None, item.get('updated_at')
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
        elif platform == 'workable':
            data = fetch_json(s, f'https://www.workable.com/api/accounts/{token}')
            if not isinstance(data, dict) or not isinstance(data.get('jobs'), list):
                raise ValueError('Unexpected Workable JSON schema')
            raw = data['jobs']
            # Some public Workable variants cap the result count: don't claim completeness.
            if len(raw) >= 30:
                note = 'POSSIBLY_TRUNCATED_WORKABLE_FEED'
            else:
                note = 'OK'
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
        note = 'OK'
    jobs = [j for item in raw if (j := normalize(platform, company, item))]
    skipped = len(raw) - len(jobs)
    if raw and not jobs:
        raise ValueError(f'All {len(raw)} returned rows were unusable or unlisted; verify schema')
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
    if published and start <= published <= now:
        return 'RECENT_VERIFY_PUBLICATION'
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
    lines += ['', 'Interpretation: CONFIRMED_24H uses an actual creation timestamp. '
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
                matched = 0
                for job in jobs:
                    if KEYWORDS and not any(k in job.title.casefold() for k in KEYWORDS):
                        continue
                    matched += 1
                    flag = classify(job, known_ids, has_baseline, start, now)
                    if flag:
                        results.append({'company':job.company, 'title':job.title, 'location':job.location,
                                        'status':flag, 'date_utc':job.created_at or job.published_at or 'Unknown',
                                        'url':job.url})
                # Do not advance baseline when a source may be truncated.
                safe = not note.startswith(('POSSIBLY_TRUNCATED', 'PAGINATION_LIMIT'))
                if safe:
                    updates[source_key] = {'ids':sorted({job.key for job in jobs}), 'last_success_utc':now.isoformat()}
                statuses.append({'company':company,'platform':platform,'count':len(jobs),
                                 'matched_titles':matched,'status':note if safe else 'PARTIAL: '+note})
                LOG.info('%s [%s]: %d jobs, %d matching titles, %s', company, platform, len(jobs), matched, note)
            except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
                statuses.append({'company':company,'platform':platform,'count':0,
                                 'matched_titles':0,'status':f'ERROR: {str(exc)[:220]}'})
                LOG.warning('%s [%s]: %s', company, platform, exc)
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
