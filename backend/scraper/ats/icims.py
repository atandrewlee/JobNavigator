"""Public iCIMS HTML boards, with complete-or-error pagination.

Reads the same public search URL over HTTP; never interacts with challenges.
Unrecognized/blocked/partial responses raise instead of masquerading as no jobs.
"""
import re
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup


def is_icims(url):
    p = urlsplit(url)
    return p.scheme == 'https' and bool(p.hostname and p.hostname.endswith('.icims.com')) and p.path.rstrip('/') == '/jobs/search' and not p.username and not p.password and p.port in (None, 443)


def parse_page(html, url):
    soup = BeautifulSoup(html, 'html.parser')
    for node in soup(['script', 'style']):
        node.decompose()
    text = soup.get_text(' ', strip=True)
    if any(s in text.casefold() for s in ('confirm you are human', 'verify you are human', 'security check before continuing', 'access denied')):
        raise ValueError('iCIMS blocked by a human-verification/access challenge')
    matches = [re.search(r'Page\s+(\d+)\s+of\s+(\d+)', h.get_text(' ', strip=True))
               for h in soup.select('.iCIMS_SubHeader_Jobs')]
    match = next((m for m in matches if m), None)
    if not match:
        # Explicit empty-board message only; never accept an arbitrary empty page.
        empty = soup.select_one('.iCIMS_NoJobs')
        if empty and re.search(r'no (jobs|results|positions)', empty.get_text(' ', strip=True), re.I):
            return [], 1, 1
        raise ValueError('iCIMS response lacks verified search pagination/empty state')
    current, total = map(int, match.groups())
    if not 1 <= current <= total:
        raise ValueError('iCIMS invalid pagination')
    jobs = {}
    for a in soup.select('a[href]'):
        p = urlsplit(urljoin(url, a['href']))
        if not re.fullmatch(r'/jobs/\d+/[^/]+/job/?', p.path):
            continue
        if p.scheme != 'https' or not p.hostname or not p.hostname.endswith('.icims.com') or p.username or p.password:
            raise ValueError('iCIMS job link is outside the public ATS')
        heading = a.find(['h2', 'h3', 'h4'])
        title = heading.get_text(' ', strip=True) if heading else ''
        if not title or len(title) > 500:
            raise ValueError('iCIMS job title cannot be verified')
        canonical = urlunsplit((p.scheme, p.netloc, p.path, '', ''))
        jobs[canonical] = {'title': title, 'url': canonical}
    if not jobs:
        raise ValueError('iCIMS paginated results unexpectedly contain no verified job links')
    return list(jobs.values()), current, total


async def scrape(url, debug=False, max_pages=None):
    if not is_icims(url):
        raise ValueError('Not a supported public iCIMS search board')
    limit = min(max_pages or 5, 20)
    p = urlsplit(url)
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    query.pop('pr', None)
    jobs, seen, total = [], set(), None
    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
        for page in range(limit):
            page_url = urlunsplit((p.scheme, p.netloc, p.path, urlencode(dict(query, pr=str(page))), ''))
            async with client.stream('GET', page_url) as response:
                if response.status_code != 200:
                    raise ValueError(f'iCIMS HTTP {response.status_code}; coverage not verified')
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 8 * 1024 * 1024:
                        raise ValueError('iCIMS response exceeds size limit')
            extracted, current, count = parse_page(content.decode('utf-8'), page_url)
            if current != page + 1 or (total is not None and count != total):
                raise ValueError('iCIMS pagination changed/repeated; coverage incomplete')
            total = count
            if total > limit:
                raise ValueError('iCIMS page limit would truncate results; coverage incomplete')
            new = [j for j in extracted if j['url'] not in seen]
            if page and not new:
                raise ValueError('iCIMS pagination repeated jobs; coverage incomplete')
            jobs.extend(new)
            seen.update(j['url'] for j in new)
            if current == total:
                coverage = {'url': url, 'complete': True, 'pages': total,
                            'jobs_found': len(jobs), 'empty': not jobs, 'method': 'icims_http'}
                return (jobs, [], coverage) if debug else jobs
    raise ValueError('iCIMS coverage incomplete')
