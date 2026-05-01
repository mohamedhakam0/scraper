"""
MENA Automation & Control Engineer Job Tracker  v2
───────────────────────────────────────────────────
Sources:
  A) Direct company career sites (no login needed)
     • ABB          → Phenom People API
     • Honeywell    → Oracle HCM API
     • Emerson      → Oracle HCM API
     • Rockwell     → Workday CXS API
     • Yokogawa     → Workday CXS API
     • Siemens      → jobs.siemens.com REST API

  B) Job boards via JobSpy
     • LinkedIn, Indeed, Bayt, Wuzzuf (Google)

Schedule : daily via GitHub Actions
Storage  : jobs.db  (SQLite, committed to repo)
Output   : jobs_report.html  (GitHub Pages)
"""

import re
import time
import uuid
import sqlite3
import requests
from bs4 import BeautifulSoup
from datetime import date, datetime

# JobSpy is optional — job boards will be skipped if not installed
try:
    import pandas as pd
    from jobspy import scrape_jobs as jobspy_scrape
    JOBSPY_AVAILABLE = True
except ImportError:
    JOBSPY_AVAILABLE = False

# ─────────────────────────────────────────────
# SHARED CONFIG
# ─────────────────────────────────────────────

TARGET_COMPANIES = [
    "Honeywell", "ABB", "Emerson", "Rockwell Automation",
    "Siemens", "Schneider Electric", "Yokogawa",
    "Endress+Hauser", "AVEVA", "Invensys", "Aspentech",
]

SEARCH_TERMS = [
    "automation engineer",
    "control engineer",
    "instrumentation engineer",
    "DCS engineer",
    "SCADA engineer",
    "PLC engineer",
    "process control engineer",
]

MENA_KEYWORDS = [
    "egypt", "cairo", "alexandria",
    "saudi", "ksa", "riyadh", "jeddah",
    "uae", "dubai", "abu dhabi", "united arab emirates",
    "qatar", "doha", "kuwait", "bahrain", "manama",
    "oman", "muscat", "jordan", "amman", "iraq",
    "lebanon", "beirut", "libya", "tunisia",
    "algeria", "morocco", "sudan", "middle east",
    "mena", "gcc", "north africa",
]

CONTROL_KEYWORDS = [
    "automation", "control", "instrumentation", "plc", "scada",
    "dcs", "process control", "instrument", "commissioning",
    "field engineer", "system engineer", "hmi", "ots",
]

SIEMENS_MENA_COUNTRIES = [
    "Egypt", "Saudi Arabia", "United Arab Emirates", "Qatar",
    "Kuwait", "Bahrain", "Oman", "Jordan", "Iraq", "Morocco",
]

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

DB_PATH = "jobs.db"
HTML_PATH = "jobs_report.html"


# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────

def is_mena(text: str) -> bool:
    t = text.lower()
    return any(k in t for k in MENA_KEYWORDS)


def is_relevant_role(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in CONTROL_KEYWORDS)


def normalize(company, title, location, description, url, source, job_id=None):
    return {
        "id": (job_id or url)[:200],
        "title": title or "",
        "company": company or "",
        "location": location or "",
        "job_type": "",
        "description": (description or "")[:3000],
        "job_url": url or "",
        "date_posted": "",
        "date_found": date.today().isoformat(),
        "source": source or "",
    }


# ─────────────────────────────────────────────
# DATABASE
# ─────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            id          TEXT PRIMARY KEY,
            title       TEXT,
            company     TEXT,
            location    TEXT,
            job_type    TEXT,
            description TEXT,
            job_url     TEXT,
            date_posted TEXT,
            date_found  TEXT,
            source      TEXT
        )
    """)
    conn.commit()
    return conn


def save_jobs(conn, jobs: list) -> int:
    new_count = 0
    for job in jobs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO jobs
                (id, title, company, location, job_type, description,
                 job_url, date_posted, date_found, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(job.get("id", job.get("job_url", "")))[:200],
                str(job.get("title", "")),
                str(job.get("company", "")),
                str(job.get("location", "")),
                str(job.get("job_type", "")),
                str(job.get("description", ""))[:3000],
                str(job.get("job_url", "")),
                str(job.get("date_posted", "")),
                str(job.get("date_found", date.today().isoformat())),
                str(job.get("source", "")),
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                new_count += 1
        except Exception as e:
            print(f"  [DB ERROR] {e}")
    conn.commit()
    return new_count


# ─────────────────────────────────────────────
# SCRAPER A1 — ABB (Phenom People)
# ─────────────────────────────────────────────

def scrape_abb():
    print("\n[ABB] Phenom People API …")
    jobs_found = []
    page = 0
    size = 50

    while True:
        try:
            payload = {
                "lang": "en_global", "deviceType": "desktop",
                "country": "global", "pageName": "search-results",
                "size": size, "from": page * size,
                "jobs": True, "counts": True,
                "all_fields": ["category", "country", "city", "type"],
                "clearAll": False, "jdsource": "facets",
                "isSliderEnable": False, "pageId": "page20",
                "siteType": "external", "keywords": "",
                "global": True, "selected_fields": {},
                "sort": {"order": "desc", "field": "postedDate"},
                "locationData": {}, "refNum": "ABB1GLOBAL",
                "ddoKey": "refineSearch",
            }
            resp = requests.post(
                "https://careers.abb/widgets",
                json=payload,
                headers={**HTTP_HEADERS, "Content-Type": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            result = data.get("refineSearch", {})
            jobs_page = result.get("data", {}).get("jobs", [])
            total = result.get("totalHits", 0)

            if not jobs_page:
                break

            for job in jobs_page:
                location = job.get("location", "") or ""
                title = job.get("title", "") or ""
                if is_mena(location) and is_relevant_role(title):
                    seq = job.get("jobSeqNo", "")
                    slug = title.lower().replace(" ", "-")
                    job_url = f"https://careers.abb/global/en/job/{seq}/{slug}"
                    jobs_found.append(normalize(
                        "ABB", title, location,
                        job.get("descriptionTeaser", ""),
                        job_url, "ABB Careers",
                        f"abb_{seq}",
                    ))

            print(f"  Page {page}: {len(jobs_page)} raw | {total} total | MENA hits: {len(jobs_found)}")
            if (page + 1) * size >= total:
                break
            page += 1
            time.sleep(0.8)

        except Exception as e:
            print(f"  [ABB ERROR] {e}")
            break

    print(f"  → {len(jobs_found)} jobs from ABB")
    return jobs_found


# ─────────────────────────────────────────────
# SCRAPER A2 — Workday (Rockwell + Yokogawa)
# ─────────────────────────────────────────────

WORKDAY_COMPANIES = [
    {
        "company": "Rockwell Automation",
        "tenant": "rockwellautomation",
        "site": "External_Rockwell_Automation",
        "wd": "wd1",
    },
    {
        "company": "Yokogawa",
        "tenant": "yokogawa",
        "site": "yokogawa-career-site",
        "wd": "wd3",
    },
]


def scrape_workday_company(cfg):
    company = cfg["company"]
    tenant  = cfg["tenant"]
    site    = cfg["site"]
    wd      = cfg["wd"]
    print(f"\n[{company}] Workday CXS API …")
    jobs_found = []
    offset = 0
    limit  = 50

    while True:
        try:
            url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
            payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
            resp = requests.post(
                url, json=payload,
                headers={
                    **HTTP_HEADERS,
                    "Content-Type": "application/json",
                    "Referer": f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}",
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            postings = data.get("jobPostings", [])
            total    = data.get("total", 0)

            if not postings:
                break

            for p in postings:
                title    = p.get("title", "") or ""
                location = p.get("locationsText", "") or ""
                ext_path = p.get("externalPath", "")
                job_url  = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{ext_path}"

                if is_mena(location) and is_relevant_role(title):
                    jobs_found.append(normalize(
                        company, title, location, "",
                        job_url, f"{company} Careers (Workday)",
                        f"{tenant}_{ext_path}",
                    ))

            print(f"  offset {offset}: {len(postings)} raw | {total} total | MENA hits: {len(jobs_found)}")
            if offset + limit >= total:
                break
            offset += limit
            time.sleep(1.2)

        except Exception as e:
            print(f"  [{company} ERROR] {e}")
            break

    print(f"  → {len(jobs_found)} jobs from {company}")
    return jobs_found


def scrape_all_workday():
    results = []
    for cfg in WORKDAY_COMPANIES:
        results.extend(scrape_workday_company(cfg))
    return results


# ─────────────────────────────────────────────
# SCRAPER A3 — Oracle HCM (Honeywell + Emerson)
# ─────────────────────────────────────────────

ORACLE_COMPANIES = [
    {
        "company": "Honeywell",
        "domain": "ibqbjb.fa.ocs.oraclecloud.com",
        "site_number": "CX_1",
    },
    {
        "company": "Emerson",
        "domain": "hdjq.fa.us2.oraclecloud.com",
        "site_number": "CX_1",
    },
]


def scrape_oracle_company(cfg):
    company     = cfg["company"]
    domain      = cfg["domain"]
    site_number = cfg["site_number"]
    print(f"\n[{company}] Oracle HCM API …")
    jobs_found = []
    offset = 0
    limit  = 100

    while True:
        try:
            url = f"https://{domain}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
            params = {
                "onlyData": "true",
                "expand": "requisitionList.workLocation,requisitionList.secondaryLocations",
                "finder": f"findReqs;siteNumber={site_number}",
                "facetsList": "LOCATIONS;CATEGORIES;ORGANIZATIONS;POSTING_DATES",
                "limit": limit,
                "offset": offset,
            }
            resp = requests.get(
                url, params=params,
                headers={
                    **HTTP_HEADERS,
                    "ora-irc-cx-userid": str(uuid.uuid4()),
                    "ora-irc-language": "en",
                    "content-type": "application/vnd.oracle.adf.resourceitem+json;charset=utf-8",
                },
                timeout=25,
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("items", [])
            if not items:
                break

            total    = items[0].get("TotalJobsCount", 0)
            req_list = items[0].get("requisitionList", [])

            if not req_list:
                break

            for job in req_list:
                title    = job.get("Title", "") or ""
                location = job.get("PrimaryLocation", "") or ""
                country  = job.get("PrimaryLocationCountry", "") or ""
                combined = f"{location} {country}".strip()
                job_id   = str(job.get("Id", ""))
                job_url  = (
                    f"https://{domain}/hcmUI/CandidateExperience/en"
                    f"/sites/{site_number}/job/{job_id}"
                )

                if is_mena(combined) and is_relevant_role(title):
                    jobs_found.append(normalize(
                        company, title, combined,
                        job.get("ShortDescriptionStr", ""),
                        job_url, f"{company} Careers (Oracle HCM)",
                        f"{company.lower()}_{job_id}",
                    ))

            print(f"  offset {offset}: {len(req_list)} raw | {total} total | MENA hits: {len(jobs_found)}")

            if not data.get("hasMore", False) or offset + limit >= total:
                break
            offset += limit
            time.sleep(1.0)

        except Exception as e:
            print(f"  [{company} ERROR] {e}")
            break

    print(f"  → {len(jobs_found)} jobs from {company}")
    return jobs_found


def scrape_all_oracle():
    results = []
    for cfg in ORACLE_COMPANIES:
        results.extend(scrape_oracle_company(cfg))
    return results


# ─────────────────────────────────────────────
# SCRAPER A4 — Siemens (jobs.siemens.com)
# ─────────────────────────────────────────────

def scrape_siemens():
    print("\n[Siemens] jobs.siemens.com REST API …")
    jobs_found = []

    for country in SIEMENS_MENA_COUNTRIES:
        try:
            resp = requests.get(
                "https://jobs.siemens.com/api/apply/v2/jobs",
                params={
                    "domain": "siemens.com",
                    "start": 0, "num": 100,
                    "location": country,
                    "sort_by": "relevance",
                },
                headers=HTTP_HEADERS,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            for p in data.get("positions", []):
                title = p.get("name", "") or ""
                loc   = p.get("location", "") or ""
                desc  = p.get("description", "") or ""
                jid   = p.get("id", "")
                job_url = (
                    p.get("canonicalPositionUrl")
                    or f"https://jobs.siemens.com/jobs/{jid}"
                )

                if is_relevant_role(title):
                    clean_desc = BeautifulSoup(desc, "html.parser").get_text()[:3000] if desc else ""
                    jobs_found.append(normalize(
                        "Siemens", title, loc, clean_desc,
                        job_url, "Siemens Careers",
                        f"siemens_{jid}",
                    ))

            print(f"  {country}: {len(data.get('positions', []))} raw")
            time.sleep(0.5)

        except Exception as e:
            print(f"  [Siemens/{country} ERROR] {e}")

    print(f"  → {len(jobs_found)} jobs from Siemens")
    return jobs_found


# ─────────────────────────────────────────────
# SCRAPER B — Job boards (LinkedIn/Indeed/Bayt/Wuzzuf)
# ─────────────────────────────────────────────

def _is_mena_row(row) -> bool:
    text = " ".join([
        str(row.get("location", "")),
        str(row.get("country", "")),
        str(row.get("city", "")),
        str(row.get("description", ""))[:500],
    ]).lower()
    return any(kw in text for kw in MENA_KEYWORDS)


def _is_target_company_row(row) -> bool:
    company = str(row.get("company", "")).lower()
    return any(c.lower() in company for c in TARGET_COMPANIES)


def scrape_job_boards():
    if not JOBSPY_AVAILABLE:
        print("  [SKIP] python-jobspy not available")
        return []

    print("\n[Job Boards] LinkedIn / Indeed / Bayt / Wuzzuf …")
    all_dfs = []

    for term in SEARCH_TERMS:
        print(f"  🔍 '{term}'")
        try:
            df = jobspy_scrape(
                site_name=["linkedin", "indeed", "bayt"],
                search_term=term,
                results_wanted=40,
                hours_old=72,
                description_format="markdown",
            )
            if df is not None and not df.empty:
                all_dfs.append(df)
        except Exception as e:
            print(f"    [ERROR] {e}")

    # Wuzzuf via Google Jobs
    try:
        wdf = jobspy_scrape(
            site_name=["google"],
            google_search_term=(
                "automation control engineer jobs Egypt "
                "Wuzzuf site:wuzzuf.net"
            ),
            results_wanted=20,
            hours_old=72,
        )
        if wdf is not None and not wdf.empty:
            all_dfs.append(wdf)
            print(f"  Wuzzuf: {len(wdf)} raw")
    except Exception as e:
        print(f"  [Wuzzuf ERROR] {e}")

    if not all_dfs:
        return []

    combined = pd.concat(all_dfs, ignore_index=True)
    combined = combined[combined.apply(_is_target_company_row, axis=1)]
    combined = combined[combined.apply(_is_mena_row, axis=1)]

    results = []
    for _, row in combined.iterrows():
        loc = " ".join(filter(None, [
            str(row.get("city", "")),
            str(row.get("state", "")),
            str(row.get("country", "")),
        ])).strip()
        results.append({
            "id": str(row.get("id", row.get("job_url", "")))[:200],
            "title": str(row.get("title", "")),
            "company": str(row.get("company", "")),
            "location": loc,
            "job_type": str(row.get("job_type", "")),
            "description": str(row.get("description", ""))[:3000],
            "job_url": str(row.get("job_url", "")),
            "date_posted": str(row.get("date_posted", "")),
            "date_found": date.today().isoformat(),
            "source": str(row.get("site", "jobspy")),
        })

    print(f"  → {len(results)} jobs from job boards (after filters)")
    return results


# ─────────────────────────────────────────────
# HTML REPORT
# ─────────────────────────────────────────────

def generate_html(conn):
    rows = conn.execute("""
        SELECT title, company, location, job_type, date_posted,
               date_found, job_url, description, source
        FROM jobs
        ORDER BY date_found DESC, date_posted DESC
        LIMIT 600
    """).fetchall()

    source_counts = {}
    for r in rows:
        src = r[8] or "unknown"
        source_counts[src] = source_counts.get(src, 0) + 1

    cards = ""
    for r in rows:
        title, company, location, job_type, date_posted, \
            date_found, url, desc, source = r
        desc_clean   = (desc or "").replace("<", "&lt;").replace(">", "&gt;")
        desc_preview = desc_clean[:450]
        jt_badge     = f'<span>💼 {job_type}</span>' if job_type and job_type not in ("None", "") else ""
        dp_badge     = f'<span>🗓 {date_posted}</span>' if date_posted and date_posted not in ("None", "") else ""

        cards += f"""
        <div class="card" data-source="{source}">
            <div class="card-header">
                <div>
                    <span class="badge">{company}</span>
                    <span class="badge src">{source or ''}</span>
                </div>
                <span class="date">Found: {date_found}</span>
            </div>
            <h3><a href="{url}" target="_blank" rel="noopener">{title}</a></h3>
            <div class="meta">
                <span>📍 {location}</span>
                {dp_badge}
                {jt_badge}
            </div>
            <p class="desc">{desc_preview}{"…" if len(desc_clean) > 450 else ""}</p>
            <a class="apply-btn" href="{url}" target="_blank" rel="noopener">View &amp; Apply →</a>
        </div>"""

    total_db    = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    source_pills = "".join(
        f'<span class="pill" onclick="setSource(this,\'{s}\')">{s} <b>{n}</b></span>'
        for s, n in sorted(source_counts.items(), key=lambda x: -x[1])
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MENA Automation Jobs Tracker</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;padding:20px}}
h1{{color:#00c8ff;margin-bottom:4px;font-size:24px}}
.sub{{color:#888;margin-bottom:18px;font-size:13px}}
.stats{{background:#1a1d27;border-radius:10px;padding:14px 20px;margin-bottom:16px;
         display:flex;gap:28px;flex-wrap:wrap;border:1px solid #2a2d3a}}
.stat{{display:flex;flex-direction:column}}
.sv{{font-size:26px;font-weight:700;color:#00c8ff}}
.sl{{font-size:12px;color:#888}}
.pills{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px}}
.pill{{background:#1a1d27;border:1px solid #2a2d3a;color:#ccc;padding:5px 12px;
        border-radius:20px;font-size:12px;cursor:pointer;transition:border-color .2s}}
.pill:hover,.pill.active{{border-color:#00c8ff;color:#00c8ff}}
.sb{{width:100%;padding:10px 16px;border-radius:8px;border:1px solid #2a2d3a;
     background:#1a1d27;color:#e0e0e0;font-size:15px;margin-bottom:16px}}
#cnt{{color:#888;font-size:13px;margin-bottom:12px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(390px,1fr));gap:14px}}
.card{{background:#1a1d27;border:1px solid #2a2d3a;border-radius:12px;padding:16px;
        transition:border-color .2s}}
.card:hover{{border-color:#00c8ff}}
.card-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
.badge{{background:#00c8ff22;color:#00c8ff;padding:3px 9px;border-radius:20px;
         font-size:11px;font-weight:600;margin-right:5px}}
.badge.src{{background:#ffffff11;color:#aaa}}
.date{{font-size:11px;color:#555}}
h3 a{{color:#e0e0e0;text-decoration:none;font-size:15px;line-height:1.4}}
h3 a:hover{{color:#00c8ff}}
.meta{{display:flex;gap:12px;margin:8px 0;font-size:12px;color:#888;flex-wrap:wrap}}
.desc{{font-size:12px;color:#aaa;line-height:1.6;margin:8px 0;
        max-height:90px;overflow:hidden}}
.apply-btn{{display:inline-block;margin-top:10px;padding:6px 14px;
             background:#00c8ff22;color:#00c8ff;border-radius:6px;
             text-decoration:none;font-size:12px;border:1px solid #00c8ff44;
             transition:background .2s}}
.apply-btn:hover{{background:#00c8ff44}}
.hidden{{display:none!important}}
.foot{{text-align:right;font-size:11px;color:#444;margin-top:22px}}
</style>
</head>
<body>
<h1>🌍 MENA Automation &amp; Control Engineer Jobs</h1>
<p class="sub">
  Daily auto-tracker · ABB · Honeywell · Emerson · Rockwell · Yokogawa ·
  Siemens · LinkedIn · Indeed · Bayt · Wuzzuf
</p>

<div class="stats">
  <div class="stat"><span class="sv">{total_db}</span><span class="sl">Total Tracked</span></div>
  <div class="stat"><span class="sv">{len(rows)}</span><span class="sl">Showing</span></div>
  <div class="stat"><span class="sv">{datetime.now().strftime('%b %d')}</span><span class="sl">Last Updated</span></div>
</div>

<div class="pills">
  <span class="pill active" onclick="setSource(this,null)">All sources</span>
  {source_pills}
</div>

<input class="sb" id="search" type="text"
       placeholder="Search title, company, location…" oninput="render()">
<p id="cnt"></p>
<div class="grid" id="grid">{cards}</div>
<p class="foot">Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</p>

<script>
let src = null;
function setSource(el, s) {{
  src = s;
  document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  render();
}}
function render() {{
  const q = document.getElementById('search').value.toLowerCase();
  let n = 0;
  document.querySelectorAll('.card').forEach(c => {{
    const ok = (!q || c.innerText.toLowerCase().includes(q))
            && (!src || c.dataset.source === src);
    c.classList.toggle('hidden', !ok);
    if (ok) n++;
  }});
  document.getElementById('cnt').textContent = n + ' job' + (n !== 1 ? 's' : '') + ' shown';
}}
render();
</script>
</body>
</html>"""

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📄 HTML saved → {HTML_PATH}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  MENA Automation Job Tracker  v2  (single-file)")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    conn      = init_db()
    total_new = 0

    # ── A. Direct career sites ────────────────────────────────
    print("\n── DIRECT CAREER SITES ─────────────────────────────────")
    direct_jobs = []
    direct_jobs.extend(scrape_abb())
    direct_jobs.extend(scrape_all_workday())
    direct_jobs.extend(scrape_all_oracle())
    direct_jobs.extend(scrape_siemens())
    new_direct = save_jobs(conn, direct_jobs)
    total_new += new_direct
    print(f"\n  Saved {new_direct} new jobs from direct sites")

    # ── B. Job boards ─────────────────────────────────────────
    print("\n── JOB BOARDS ───────────────────────────────────────────")
    board_jobs = scrape_job_boards()
    new_boards = save_jobs(conn, board_jobs)
    total_new += new_boards
    print(f"\n  Saved {new_boards} new jobs from job boards")

    # ── Summary ───────────────────────────────────────────────
    total_db = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"\n{'─'*60}")
    print(f"  ✅ New this run  : {total_new}")
    print(f"  📦 Total in DB   : {total_db}")
    print(f"{'─'*60}")

    generate_html(conn)
    conn.close()
    print("\n🏁 Done.")
