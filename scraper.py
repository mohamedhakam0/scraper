"""
MENA Automation Companies Job Tracker  v3
──────────────────────────────────────────
Pulls ALL jobs (any role) from target companies in MENA countries only.
Strict location matching — no false positives like Romania.

Sources:
  • ABB          → Phenom People API
  • Honeywell    → Oracle HCM API
  • Emerson      → Oracle HCM API
  • Rockwell     → Workday CXS API
  • Yokogawa     → Workday CXS API
  • Siemens      → jobs.siemens.com REST API
  • LinkedIn / Indeed / Bayt / Wuzzuf → JobSpy
"""

import re
import time
import uuid
import sqlite3
import requests
from bs4 import BeautifulSoup
from datetime import date, datetime

try:
    import pandas as pd
    from jobspy import scrape_jobs as jobspy_scrape
    JOBSPY_AVAILABLE = True
except ImportError:
    JOBSPY_AVAILABLE = False

# ─────────────────────────────────────────────
# MENA COUNTRY DEFINITIONS
# strict set — must match a real MENA country/city
# ─────────────────────────────────────────────

# Each entry is a tuple: (display_name, [keywords that identify it])
MENA_COUNTRIES = [
    ("Egypt",                ["egypt", "cairo", "alexandria", "giza", "suez", "luxor", "aswan"]),
    ("Saudi Arabia",         ["saudi arabia", "saudi", "ksa", "riyadh", "jeddah", "dammam", "khobar", "dhahran", "mecca", "medina", "yanbu", "jubail"]),
    ("UAE",                  ["united arab emirates", "uae", "dubai", "abu dhabi", "sharjah", "ajman", "ras al khaimah", "fujairah", "al ain"]),
    ("Qatar",                ["qatar", "doha", "al wakrah", "al khor"]),
    ("Kuwait",               ["kuwait", "kuwait city"]),
    ("Bahrain",              ["bahrain", "manama", "riffa"]),
    ("Oman",                 ["oman", "muscat", "salalah", "sohar"]),
    ("Jordan",               ["jordan", "amman", "aqaba", "zarqa"]),
    ("Iraq",                 ["iraq", "baghdad", "basra", "erbil", "kirkuk"]),
    ("Lebanon",              ["lebanon", "beirut"]),
    ("Libya",                ["libya", "tripoli", "benghazi"]),
    ("Tunisia",              ["tunisia", "tunis"]),
    ("Algeria",              ["algeria", "algiers", "oran"]),
    ("Morocco",              ["morocco", "casablanca", "rabat", "marrakech", "tangier"]),
    ("Sudan",                ["sudan", "khartoum"]),
    ("Yemen",                ["yemen", "sanaa", "aden"]),
    ("Palestine",            ["palestine", "west bank", "gaza", "ramallah"]),
    ("Syria",                ["syria", "damascus", "aleppo"]),
]

# Flat keyword list for fast matching
MENA_KEYWORDS = [kw for _, kws in MENA_COUNTRIES for kw in kws]

# Country name lookup (keyword → display name)
KEYWORD_TO_COUNTRY = {}
for country_name, kws in MENA_COUNTRIES:
    for kw in kws:
        KEYWORD_TO_COUNTRY[kw] = country_name

# For Siemens API — use canonical country names
SIEMENS_MENA_COUNTRIES = [
    "Egypt", "Saudi Arabia", "United Arab Emirates", "Qatar",
    "Kuwait", "Bahrain", "Oman", "Jordan", "Iraq", "Morocco", "Libya",
]

TARGET_COMPANIES = [
    "Honeywell", "ABB", "Emerson", "Rockwell Automation",
    "Siemens", "Schneider Electric", "Yokogawa",
]

SEARCH_TERMS = [
    "engineer", "manager", "specialist", "technician",
    "analyst", "consultant", "supervisor",
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

DB_PATH    = "jobs.db"
HTML_PATH  = "jobs_report.html"


# ─────────────────────────────────────────────
# LOCATION HELPERS  — strict matching
# ─────────────────────────────────────────────

def detect_country(location_text: str) -> str | None:
    """Return MENA country display name if location_text matches, else None."""
    t = location_text.lower()
    for kw, country in KEYWORD_TO_COUNTRY.items():
        # Use word-boundary style check: keyword must appear as a standalone phrase
        # (not as a substring of another word — e.g. "oman" inside "roman")
        pattern = r'(?<![a-z])' + re.escape(kw) + r'(?![a-z])'
        if re.search(pattern, t):
            return country
    return None


def is_mena(location_text: str) -> bool:
    return detect_country(location_text) is not None


# ─────────────────────────────────────────────
# NORMALIZE
# ─────────────────────────────────────────────

def normalize(company, title, location, description, url, source, job_id=None):
    country = detect_country(location) or ""
    return {
        "id":          (job_id or url)[:200],
        "title":       (title       or "").strip(),
        "company":     (company     or "").strip(),
        "location":    (location    or "").strip(),
        "country":     country,
        "description": (description or "")[:3000],
        "job_url":     (url         or "").strip(),
        "date_posted": "",
        "date_found":  date.today().isoformat(),
        "source":      (source      or "").strip(),
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
            country     TEXT,
            description TEXT,
            job_url     TEXT,
            date_posted TEXT,
            date_found  TEXT,
            source      TEXT
        )
    """)
    # Add country column if upgrading from older schema
    try:
        conn.execute("ALTER TABLE jobs ADD COLUMN country TEXT DEFAULT ''")
    except Exception:
        pass
    conn.commit()
    return conn


def save_jobs(conn, jobs: list) -> int:
    new_count = 0
    for job in jobs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO jobs
                (id, title, company, location, country, description,
                 job_url, date_posted, date_found, source)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                str(job.get("id",          ""))[:200],
                str(job.get("title",       "")),
                str(job.get("company",     "")),
                str(job.get("location",    "")),
                str(job.get("country",     "")),
                str(job.get("description", ""))[:3000],
                str(job.get("job_url",     "")),
                str(job.get("date_posted", "")),
                str(job.get("date_found",  date.today().isoformat())),
                str(job.get("source",      "")),
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                new_count += 1
        except Exception as e:
            print(f"  [DB ERROR] {e}")
    conn.commit()
    return new_count


# ─────────────────────────────────────────────
# SCRAPER 1 — ABB (Phenom People)
# ─────────────────────────────────────────────

def scrape_abb():
    print("\n[ABB] Phenom People API …")
    found = []
    page, size = 0, 50

    while True:
        try:
            resp = requests.post(
                "https://careers.abb/widgets",
                json={
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
                },
                headers={**HTTP_HEADERS, "Content-Type": "application/json"},
                timeout=20,
            )
            resp.raise_for_status()
            data  = resp.json()
            res   = data.get("refineSearch", {})
            page_jobs = res.get("data", {}).get("jobs", [])
            total = res.get("totalHits", 0)

            if not page_jobs:
                break

            for job in page_jobs:
                loc   = job.get("location", "") or ""
                title = job.get("title",    "") or ""
                if is_mena(loc):
                    seq  = job.get("jobSeqNo", "")
                    slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
                    url  = f"https://careers.abb/global/en/job/{seq}/{slug}"
                    found.append(normalize(
                        "ABB", title, loc,
                        job.get("descriptionTeaser", ""),
                        url, "ABB Careers", f"abb_{seq}",
                    ))

            print(f"  page {page}: {len(page_jobs)} raw | {total} total | MENA: {len(found)}")
            if (page + 1) * size >= total:
                break
            page += 1
            time.sleep(0.8)
        except Exception as e:
            print(f"  [ABB ERROR] {e}")
            break

    print(f"  → {len(found)} ABB jobs")
    return found


# ─────────────────────────────────────────────
# SCRAPER 2 — Workday (Rockwell + Yokogawa)
# ─────────────────────────────────────────────

WORKDAY_COMPANIES = [
    {"company": "Rockwell Automation", "tenant": "rockwellautomation",
     "site": "External_Rockwell_Automation", "wd": "wd1"},
    {"company": "Yokogawa",            "tenant": "yokogawa",
     "site": "yokogawa-career-site",          "wd": "wd3"},
]


def scrape_workday_company(cfg):
    company, tenant, site, wd = cfg["company"], cfg["tenant"], cfg["site"], cfg["wd"]
    print(f"\n[{company}] Workday CXS API …")
    found, offset, limit = [], 0, 50

    while True:
        try:
            url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
            resp = requests.post(
                url,
                json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
                headers={**HTTP_HEADERS, "Content-Type": "application/json",
                         "Referer": f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}"},
                timeout=20,
            )
            resp.raise_for_status()
            data     = resp.json()
            postings = data.get("jobPostings", [])
            total    = data.get("total", 0)

            if not postings:
                break

            for p in postings:
                title = p.get("title",         "") or ""
                loc   = p.get("locationsText", "") or ""
                path  = p.get("externalPath",  "")
                job_url = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{path}"
                if is_mena(loc):
                    found.append(normalize(
                        company, title, loc, "",
                        job_url, f"{company} Careers",
                        f"{tenant}_{path}",
                    ))

            print(f"  offset {offset}: {len(postings)} raw | {total} total | MENA: {len(found)}")
            if offset + limit >= total:
                break
            offset += limit
            time.sleep(1.2)
        except Exception as e:
            print(f"  [{company} ERROR] {e}")
            break

    print(f"  → {len(found)} {company} jobs")
    return found


# ─────────────────────────────────────────────
# SCRAPER 3 — Oracle HCM (Honeywell + Emerson)
# ─────────────────────────────────────────────

ORACLE_COMPANIES = [
    {"company": "Honeywell", "domain": "ibqbjb.fa.ocs.oraclecloud.com", "site_number": "CX_1"},
    {"company": "Emerson",   "domain": "hdjq.fa.us2.oraclecloud.com",   "site_number": "CX_1"},
]


def scrape_oracle_company(cfg):
    company, domain, site_number = cfg["company"], cfg["domain"], cfg["site_number"]
    print(f"\n[{company}] Oracle HCM API …")
    found, offset, limit = [], 0, 100

    while True:
        try:
            resp = requests.get(
                f"https://{domain}/hcmRestApi/resources/latest/recruitingCEJobRequisitions",
                params={
                    "onlyData": "true",
                    "expand": "requisitionList.workLocation,requisitionList.secondaryLocations",
                    "finder": f"findReqs;siteNumber={site_number}",
                    "facetsList": "LOCATIONS;CATEGORIES;ORGANIZATIONS;POSTING_DATES",
                    "limit": limit, "offset": offset,
                },
                headers={**HTTP_HEADERS,
                         "ora-irc-cx-userid": str(uuid.uuid4()),
                         "ora-irc-language": "en",
                         "content-type": "application/vnd.oracle.adf.resourceitem+json;charset=utf-8"},
                timeout=25,
            )
            resp.raise_for_status()
            data  = resp.json()
            items = data.get("items", [])
            if not items:
                break

            total    = items[0].get("TotalJobsCount", 0)
            req_list = items[0].get("requisitionList", [])
            if not req_list:
                break

            for job in req_list:
                title   = job.get("Title",                   "") or ""
                loc     = job.get("PrimaryLocation",         "") or ""
                country = job.get("PrimaryLocationCountry",  "") or ""
                combined = f"{loc}, {country}".strip(", ")
                jid      = str(job.get("Id", ""))
                job_url  = (f"https://{domain}/hcmUI/CandidateExperience/en"
                            f"/sites/{site_number}/job/{jid}")
                if is_mena(combined):
                    found.append(normalize(
                        company, title, combined,
                        job.get("ShortDescriptionStr", ""),
                        job_url, f"{company} Careers",
                        f"{company.lower()}_{jid}",
                    ))

            print(f"  offset {offset}: {len(req_list)} raw | {total} total | MENA: {len(found)}")
            if not data.get("hasMore", False) or offset + limit >= total:
                break
            offset += limit
            time.sleep(1.0)
        except Exception as e:
            print(f"  [{company} ERROR] {e}")
            break

    print(f"  → {len(found)} {company} jobs")
    return found


# ─────────────────────────────────────────────
# SCRAPER 4 — Siemens
# ─────────────────────────────────────────────

def scrape_siemens():
    print("\n[Siemens] jobs.siemens.com REST API …")
    found = []

    for country in SIEMENS_MENA_COUNTRIES:
        try:
            resp = requests.get(
                "https://jobs.siemens.com/api/apply/v2/jobs",
                params={"domain": "siemens.com", "start": 0, "num": 100,
                        "location": country, "sort_by": "relevance"},
                headers=HTTP_HEADERS, timeout=20,
            )
            resp.raise_for_status()
            positions = resp.json().get("positions", [])

            for p in positions:
                title = p.get("name",     "") or ""
                loc   = p.get("location", "") or ""
                desc  = p.get("description", "") or ""
                jid   = p.get("id", "")
                job_url = p.get("canonicalPositionUrl") or f"https://jobs.siemens.com/jobs/{jid}"
                clean_desc = BeautifulSoup(desc, "html.parser").get_text()[:3000] if desc else ""
                # Use the country we searched — it's definitionally MENA
                found.append(normalize(
                    "Siemens", title, f"{loc}, {country}" if loc else country,
                    clean_desc, job_url, "Siemens Careers", f"siemens_{jid}",
                ))

            print(f"  {country}: {len(positions)}")
            time.sleep(0.5)
        except Exception as e:
            print(f"  [Siemens/{country} ERROR] {e}")

    print(f"  → {len(found)} Siemens jobs")
    return found


# ─────────────────────────────────────────────
# SCRAPER 5 — Schneider Electric
# Schneider uses SmartRecruiters — public REST API
# ─────────────────────────────────────────────

SCHNEIDER_MENA_COUNTRY_CODES = [
    "EG", "SA", "AE", "QA", "KW", "BH", "OM",
    "JO", "IQ", "LB", "LY", "TN", "DZ", "MA",
]


def scrape_schneider():
    print("\n[Schneider Electric] SmartRecruiters API …")
    found = []

    for cc in SCHNEIDER_MENA_COUNTRY_CODES:
        try:
            resp = requests.get(
                "https://api.smartrecruiters.com/v1/companies/SchneiderElectric/postings",
                params={"country": cc, "limit": 100, "offset": 0},
                headers=HTTP_HEADERS, timeout=20,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for job in data.get("content", []):
                title = job.get("name", "") or ""
                city  = (job.get("location") or {}).get("city", "") or ""
                cname = (job.get("location") or {}).get("country", {}).get("label", "") or ""
                loc   = f"{city}, {cname}".strip(", ")
                jid   = job.get("id", "")
                job_url = f"https://jobs.smartrecruiters.com/SchneiderElectric/{jid}"
                found.append(normalize(
                    "Schneider Electric", title, loc, "",
                    job_url, "Schneider Electric Careers",
                    f"schneider_{jid}",
                ))
            print(f"  {cc}: {len(data.get('content', []))}")
            time.sleep(0.4)
        except Exception as e:
            print(f"  [Schneider/{cc} ERROR] {e}")

    print(f"  → {len(found)} Schneider jobs")
    return found


# ─────────────────────────────────────────────
# SCRAPER 6 — Job Boards (LinkedIn/Indeed/Bayt/Wuzzuf)
# ─────────────────────────────────────────────

def scrape_job_boards():
    if not JOBSPY_AVAILABLE:
        print("  [SKIP] python-jobspy not installed")
        return []

    print("\n[Job Boards] LinkedIn / Indeed / Bayt / Wuzzuf …")
    all_dfs = []

    for term in SEARCH_TERMS:
        for company in ["Honeywell", "ABB", "Emerson", "Rockwell Automation",
                        "Siemens", "Schneider Electric", "Yokogawa"]:
            query = f"{company} {term}"
            print(f"  🔍 '{query}'")
            try:
                df = jobspy_scrape(
                    site_name=["linkedin", "indeed", "bayt"],
                    search_term=query,
                    results_wanted=25,
                    hours_old=72,
                    description_format="markdown",
                )
                if df is not None and not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                print(f"    [ERROR] {e}")
            time.sleep(1)

    if not all_dfs:
        return []

    import pandas as pd
    combined = pd.concat(all_dfs, ignore_index=True)

    results = []
    for _, row in combined.iterrows():
        loc = " ".join(filter(None, [
            str(row.get("city", "")),
            str(row.get("state", "")),
            str(row.get("country", "")),
            str(row.get("location", "")),
        ]))
        if not is_mena(loc):
            continue
        company_val = str(row.get("company", ""))
        if not any(c.lower() in company_val.lower() for c in TARGET_COMPANIES):
            continue
        results.append({
            "id":          str(row.get("id", row.get("job_url", "")))[:200],
            "title":       str(row.get("title",    "")),
            "company":     company_val,
            "location":    loc.strip(),
            "country":     detect_country(loc) or "",
            "description": str(row.get("description", ""))[:3000],
            "job_url":     str(row.get("job_url",   "")),
            "date_posted": str(row.get("date_posted", "")),
            "date_found":  date.today().isoformat(),
            "source":      str(row.get("site", "jobspy")),
        })

    print(f"  → {len(results)} jobs from job boards")
    return results


# ─────────────────────────────────────────────
# HTML REPORT  — Ultra premium theme
# ─────────────────────────────────────────────

def generate_html(conn):
    rows = conn.execute("""
        SELECT title, company, location, country, date_posted,
               date_found, job_url, description, source
        FROM jobs
        ORDER BY date_found DESC, date_posted DESC
        LIMIT 800
    """).fetchall()

    total_db = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # Aggregate data for filters
    countries = sorted(set(r[3] for r in rows if r[3]))
    companies = sorted(set(r[1] for r in rows if r[1]))

    cards = ""
    for r in rows:
        title, company, location, country, date_posted, \
            date_found, url, desc, source = r
        desc_clean   = (desc or "").replace("<", "&lt;").replace(">", "&gt;")
        desc_preview = desc_clean[:380]
        dp = date_posted if date_posted and date_posted not in ("None","") else ""
        country_tag  = f'<span class="tag country-tag">{country}</span>' if country else ""
        dp_tag       = f'<span class="tag date-tag">{dp}</span>' if dp else ""

        cards += f"""<div class="card"
            data-company="{company}"
            data-country="{country}"
            data-posted="{date_posted or ''}"
            data-found="{date_found}">
  <div class="card-top">
    <div class="company-pill">{company}</div>
    <div class="source-label">{source or ''}</div>
  </div>
  <h3 class="card-title"><a href="{url}" target="_blank" rel="noopener">{title}</a></h3>
  <div class="card-tags">
    <span class="tag loc-tag">📍 {location}</span>
    {country_tag}
    {dp_tag}
  </div>
  <p class="card-desc">{desc_preview}{"…" if len(desc_clean) > 380 else ""}</p>
  <a class="apply-link" href="{url}" target="_blank" rel="noopener">
    View &amp; Apply <span class="arrow">→</span>
  </a>
</div>"""

    country_opts  = "".join(f'<option value="{c}">{c}</option>' for c in countries)
    company_opts  = "".join(f'<option value="{c}">{c}</option>' for c in companies)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MENA Jobs — Automation Companies</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Syne:wght@400;600;700;800&family=DM+Sans:ital,wght@0,300;0,400;0,500;1,300&display=swap" rel="stylesheet">
<style>
/* ── RESET & BASE ─────────────────────────────── */
*, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

:root {{
  --bg:        #080b10;
  --surface:   #0d1117;
  --surface2:  #131923;
  --surface3:  #1a2233;
  --border:    #1e2d40;
  --border2:   #243347;
  --accent:    #00d4ff;
  --accent2:   #0099cc;
  --accent-glow: rgba(0, 212, 255, 0.15);
  --gold:      #f0c060;
  --text:      #e8edf5;
  --text2:     #8a9bb5;
  --text3:     #526070;
  --radius:    12px;
  --radius-lg: 18px;
  --font-display: 'Syne', sans-serif;
  --font-body:    'DM Sans', sans-serif;
  --transition: 0.22s cubic-bezier(0.4, 0, 0.2, 1);
}}

html {{ scroll-behavior: smooth; }}

body {{
  font-family: var(--font-body);
  background: var(--bg);
  color: var(--text);
  min-height: 100vh;
  background-image:
    radial-gradient(ellipse 80% 50% at 50% -10%, rgba(0,180,255,0.07) 0%, transparent 70%),
    radial-gradient(ellipse 40% 30% at 90% 20%, rgba(0,100,200,0.05) 0%, transparent 60%);
}}

/* ── HEADER ───────────────────────────────────── */
.header {{
  padding: 52px 40px 36px;
  max-width: 1400px;
  margin: 0 auto;
  position: relative;
}}

.header-eyebrow {{
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.2em;
  text-transform: uppercase;
  color: var(--accent);
  margin-bottom: 16px;
  display: flex;
  align-items: center;
  gap: 10px;
}}

.header-eyebrow::before {{
  content: '';
  display: inline-block;
  width: 28px;
  height: 1.5px;
  background: var(--accent);
}}

.header h1 {{
  font-family: var(--font-display);
  font-size: clamp(32px, 5vw, 58px);
  font-weight: 800;
  line-height: 1.05;
  letter-spacing: -0.02em;
  color: var(--text);
  margin-bottom: 16px;
}}

.header h1 .highlight {{
  background: linear-gradient(135deg, var(--accent) 0%, #60b8ff 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}}

.header-sub {{
  font-size: 15px;
  color: var(--text2);
  font-weight: 300;
  max-width: 560px;
  line-height: 1.65;
}}

/* ── STATS BAR ────────────────────────────────── */
.stats-bar {{
  max-width: 1400px;
  margin: 0 auto 36px;
  padding: 0 40px;
  display: flex;
  gap: 2px;
}}

.stat-card {{
  flex: 1;
  background: var(--surface);
  border: 1px solid var(--border);
  padding: 20px 24px;
  position: relative;
  overflow: hidden;
}}

.stat-card:first-child {{ border-radius: var(--radius) 0 0 var(--radius); }}
.stat-card:last-child  {{ border-radius: 0 var(--radius) var(--radius) 0; }}

.stat-card::before {{
  content: '';
  position: absolute;
  top: 0; left: 0; right: 0;
  height: 2px;
  background: linear-gradient(90deg, transparent, var(--accent), transparent);
  opacity: 0;
  transition: opacity var(--transition);
}}

.stat-card:hover::before {{ opacity: 1; }}

.stat-value {{
  font-family: var(--font-display);
  font-size: 32px;
  font-weight: 800;
  color: var(--accent);
  letter-spacing: -0.02em;
  line-height: 1;
  margin-bottom: 4px;
}}

.stat-label {{
  font-size: 12px;
  color: var(--text3);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  font-weight: 500;
}}

/* ── CONTROLS ─────────────────────────────────── */
.controls {{
  max-width: 1400px;
  margin: 0 auto 28px;
  padding: 0 40px;
  display: grid;
  grid-template-columns: 1fr auto auto auto;
  gap: 12px;
  align-items: center;
}}

.search-wrap {{
  position: relative;
}}

.search-icon {{
  position: absolute;
  left: 16px;
  top: 50%;
  transform: translateY(-50%);
  color: var(--text3);
  font-size: 16px;
  pointer-events: none;
}}

.search-input {{
  width: 100%;
  padding: 13px 16px 13px 44px;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 14px;
  outline: none;
  transition: border-color var(--transition), box-shadow var(--transition);
}}

.search-input::placeholder {{ color: var(--text3); }}

.search-input:focus {{
  border-color: var(--accent);
  box-shadow: 0 0 0 3px var(--accent-glow);
}}

.select-wrap select {{
  appearance: none;
  padding: 13px 36px 13px 16px;
  background: var(--surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%238a9bb5' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E") no-repeat calc(100% - 14px) center;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 14px;
  cursor: pointer;
  outline: none;
  min-width: 160px;
  transition: border-color var(--transition);
}}

.select-wrap select:focus {{ border-color: var(--accent); }}
.select-wrap select option {{ background: var(--surface2); }}

.sort-wrap select {{
  appearance: none;
  padding: 13px 36px 13px 16px;
  background: var(--surface) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8'%3E%3Cpath d='M1 1l5 5 5-5' stroke='%238a9bb5' stroke-width='1.5' fill='none' stroke-linecap='round'/%3E%3C/svg%3E") no-repeat calc(100% - 14px) center;
  border: 1px solid var(--border);
  border-radius: var(--radius);
  color: var(--text);
  font-family: var(--font-body);
  font-size: 14px;
  cursor: pointer;
  outline: none;
  min-width: 160px;
  transition: border-color var(--transition);
}}

.sort-wrap select:focus {{ border-color: var(--accent); }}
.sort-wrap select option {{ background: var(--surface2); }}

/* ── COMPANY FILTER PILLS ─────────────────────── */
.company-filters {{
  max-width: 1400px;
  margin: 0 auto 24px;
  padding: 0 40px;
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}}

.cpill {{
  padding: 6px 14px;
  border: 1px solid var(--border);
  border-radius: 100px;
  font-size: 12px;
  font-weight: 500;
  color: var(--text2);
  cursor: pointer;
  transition: all var(--transition);
  background: transparent;
  font-family: var(--font-body);
  white-space: nowrap;
}}

.cpill:hover {{
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-glow);
}}

.cpill.active {{
  border-color: var(--accent);
  color: var(--accent);
  background: var(--accent-glow);
}}

/* ── RESULTS COUNT ────────────────────────────── */
.results-meta {{
  max-width: 1400px;
  margin: 0 auto 20px;
  padding: 0 40px;
  display: flex;
  align-items: center;
  justify-content: space-between;
}}

.results-count {{
  font-size: 13px;
  color: var(--text3);
  font-weight: 300;
}}

.results-count strong {{
  color: var(--text);
  font-weight: 500;
}}

/* ── GRID ─────────────────────────────────────── */
.grid {{
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 40px 60px;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
  gap: 16px;
}}

/* ── CARD ─────────────────────────────────────── */
.card {{
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 22px 22px 18px;
  display: flex;
  flex-direction: column;
  gap: 12px;
  transition: border-color var(--transition), transform var(--transition), box-shadow var(--transition);
  cursor: default;
  position: relative;
  overflow: hidden;
}}

.card::after {{
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
  background: linear-gradient(135deg, var(--accent-glow) 0%, transparent 60%);
  opacity: 0;
  transition: opacity var(--transition);
  pointer-events: none;
}}

.card:hover {{
  border-color: var(--border2);
  transform: translateY(-2px);
  box-shadow: 0 8px 40px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(0, 212, 255, 0.08);
}}

.card:hover::after {{ opacity: 1; }}

.card.hidden {{ display: none !important; }}

/* card top */
.card-top {{
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}}

.company-pill {{
  font-family: var(--font-display);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--accent);
  background: var(--accent-glow);
  border: 1px solid rgba(0, 212, 255, 0.2);
  padding: 4px 10px;
  border-radius: 6px;
  white-space: nowrap;
}}

.source-label {{
  font-size: 11px;
  color: var(--text3);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}}

/* card title */
.card-title {{
  font-family: var(--font-display);
  font-size: 16px;
  font-weight: 700;
  line-height: 1.35;
  letter-spacing: -0.01em;
}}

.card-title a {{
  color: var(--text);
  text-decoration: none;
  transition: color var(--transition);
}}

.card-title a:hover {{ color: var(--accent); }}

/* tags */
.card-tags {{
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}}

.tag {{
  font-size: 11px;
  padding: 3px 9px;
  border-radius: 6px;
  font-weight: 400;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 220px;
}}

.loc-tag     {{ background: rgba(255,255,255,0.05); color: var(--text2); border: 1px solid var(--border); }}
.country-tag {{ background: rgba(240,192,96,0.1);   color: var(--gold);  border: 1px solid rgba(240,192,96,0.2); }}
.date-tag    {{ background: rgba(0,212,255,0.08);   color: var(--accent2); border: 1px solid rgba(0,212,255,0.15); }}

/* description */
.card-desc {{
  font-size: 13px;
  color: var(--text2);
  line-height: 1.65;
  font-weight: 300;
  flex: 1;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}}

/* apply link */
.apply-link {{
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 500;
  color: var(--accent);
  text-decoration: none;
  padding: 9px 16px;
  border: 1px solid rgba(0, 212, 255, 0.25);
  border-radius: 8px;
  background: var(--accent-glow);
  align-self: flex-start;
  transition: all var(--transition);
  margin-top: auto;
}}

.apply-link:hover {{
  background: rgba(0, 212, 255, 0.2);
  border-color: rgba(0, 212, 255, 0.5);
  gap: 10px;
}}

.arrow {{ transition: transform var(--transition); }}
.apply-link:hover .arrow {{ transform: translateX(3px); }}

/* ── EMPTY STATE ──────────────────────────────── */
.empty {{
  grid-column: 1 / -1;
  padding: 80px 20px;
  text-align: center;
  color: var(--text3);
  font-size: 15px;
  display: none;
}}

.empty.visible {{ display: block; }}

/* ── FOOTER ───────────────────────────────────── */
.footer {{
  max-width: 1400px;
  margin: 0 auto;
  padding: 0 40px 40px;
  border-top: 1px solid var(--border);
  padding-top: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--text3);
}}

/* ── SCROLLBAR ────────────────────────────────── */
::-webkit-scrollbar {{ width: 6px; }}
::-webkit-scrollbar-track {{ background: var(--bg); }}
::-webkit-scrollbar-thumb {{ background: var(--border2); border-radius: 3px; }}

/* ── RESPONSIVE ───────────────────────────────── */
@media (max-width: 768px) {{
  .header, .stats-bar, .controls, .company-filters,
  .results-meta, .grid, .footer {{ padding-left: 20px; padding-right: 20px; }}
  .controls {{ grid-template-columns: 1fr; }}
  .stats-bar {{ flex-direction: column; gap: 2px; }}
  .stat-card:first-child {{ border-radius: var(--radius) var(--radius) 0 0; }}
  .stat-card:last-child  {{ border-radius: 0 0 var(--radius) var(--radius); }}
  .grid {{ grid-template-columns: 1fr; }}
}}
</style>
</head>
<body>

<!-- HEADER -->
<header class="header">
  <div class="header-eyebrow">MENA Region · Auto-updated daily</div>
  <h1>Automation Industry<br><span class="highlight">Job Intelligence</span></h1>
  <p class="header-sub">
    All open roles at Honeywell, ABB, Emerson, Rockwell Automation,
    Siemens, Schneider Electric &amp; Yokogawa across the Arab world
    and GCC — updated every morning.
  </p>
</header>

<!-- STATS -->
<div class="stats-bar">
  <div class="stat-card">
    <div class="stat-value" id="s-total">{total_db}</div>
    <div class="stat-label">Total tracked</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" id="s-shown">{len(rows)}</div>
    <div class="stat-label">Showing</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{len(countries)}</div>
    <div class="stat-label">Countries</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{datetime.now().strftime('%d %b')}</div>
    <div class="stat-label">Last updated</div>
  </div>
</div>

<!-- CONTROLS -->
<div class="controls">
  <div class="search-wrap">
    <span class="search-icon">🔍</span>
    <input class="search-input" id="search" type="text"
           placeholder="Search title, location, description…" oninput="render()">
  </div>
  <div class="select-wrap">
    <select id="country-filter" onchange="render()">
      <option value="">All countries</option>
      {country_opts}
    </select>
  </div>
  <div class="sort-wrap">
    <select id="sort-select" onchange="render()">
      <option value="found-desc">Newest found</option>
      <option value="found-asc">Oldest found</option>
      <option value="posted-desc">Newest posted</option>
      <option value="posted-asc">Oldest posted</option>
      <option value="title-asc">Title A–Z</option>
      <option value="company-asc">Company A–Z</option>
    </select>
  </div>
</div>

<!-- COMPANY PILLS -->
<div class="company-filters">
  <button class="cpill active" onclick="setCompany(this, '')">All companies</button>
  {"".join(f'<button class="cpill" onclick="setCompany(this, \\'{c}\\')">{c}</button>' for c in companies)}
</div>

<!-- RESULTS META -->
<div class="results-meta">
  <div class="results-count" id="results-count">Loading…</div>
</div>

<!-- GRID -->
<div class="grid" id="grid">
  {cards}
  <div class="empty" id="empty">No jobs match your filters.</div>
</div>

<!-- FOOTER -->
<footer class="footer">
  <span>Sources: ABB · Honeywell · Emerson · Rockwell · Yokogawa · Siemens · Schneider · LinkedIn · Indeed · Bayt · Wuzzuf</span>
  <span>Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</span>
</footer>

<script>
let activeCompany = '';

function setCompany(el, c) {{
  activeCompany = c;
  document.querySelectorAll('.cpill').forEach(p => p.classList.remove('active'));
  el.classList.add('active');
  render();
}}

function render() {{
  const q       = document.getElementById('search').value.toLowerCase().trim();
  const country = document.getElementById('country-filter').value;
  const sort    = document.getElementById('sort-select').value;

  const cards = Array.from(document.querySelectorAll('.card'));

  // Filter
  let visible = [];
  cards.forEach(c => {{
    const text = c.innerText.toLowerCase();
    const matchQ  = !q       || text.includes(q);
    const matchCo = !activeCompany || c.dataset.company === activeCompany;
    const matchCu = !country || c.dataset.country   === country;
    const show = matchQ && matchCo && matchCu;
    c.classList.toggle('hidden', !show);
    if (show) visible.push(c);
  }});

  // Sort
  const grid = document.getElementById('grid');
  visible.sort((a, b) => {{
    switch (sort) {{
      case 'found-desc':   return b.dataset.found.localeCompare(a.dataset.found);
      case 'found-asc':    return a.dataset.found.localeCompare(b.dataset.found);
      case 'posted-desc':  return (b.dataset.posted||'').localeCompare(a.dataset.posted||'');
      case 'posted-asc':   return (a.dataset.posted||'').localeCompare(b.dataset.posted||'');
      case 'title-asc':    return a.querySelector('.card-title').innerText.localeCompare(b.querySelector('.card-title').innerText);
      case 'company-asc':  return a.dataset.company.localeCompare(b.dataset.company);
      default: return 0;
    }
  }});

  visible.forEach(c => grid.appendChild(c));

  // Count
  const n = visible.length;
  document.getElementById('results-count').innerHTML =
    `Showing <strong>${{n}}</strong> of <strong>{total_db}</strong> jobs`;
  document.getElementById('s-shown').textContent = n;

  // Empty state
  document.getElementById('empty').classList.toggle('visible', n === 0);
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
    print("  MENA Automation Job Tracker  v3")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    conn      = init_db()
    total_new = 0
    all_jobs  = []

    print("\n── DIRECT CAREER SITES ─────────────────────────────────")
    all_jobs.extend(scrape_abb())
    all_jobs.extend(scrape_workday_company(WORKDAY_COMPANIES[0]))
    all_jobs.extend(scrape_workday_company(WORKDAY_COMPANIES[1]))
    all_jobs.extend(scrape_oracle_company(ORACLE_COMPANIES[0]))
    all_jobs.extend(scrape_oracle_company(ORACLE_COMPANIES[1]))
    all_jobs.extend(scrape_siemens())
    all_jobs.extend(scrape_schneider())

    new_direct = save_jobs(conn, all_jobs)
    total_new += new_direct
    print(f"\n  Saved {new_direct} new from direct sites")

    print("\n── JOB BOARDS ───────────────────────────────────────────")
    board_jobs = scrape_job_boards()
    new_boards = save_jobs(conn, board_jobs)
    total_new += new_boards
    print(f"\n  Saved {new_boards} new from job boards")

    total_db = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"\n{'─'*60}")
    print(f"  ✅ New this run  : {total_new}")
    print(f"  📦 Total in DB   : {total_db}")
    print(f"{'─'*60}")

    generate_html(conn)
    conn.close()
    print("\n🏁 Done.")
