"""
Direct career site scrapers — bypasses LinkedIn/Indeed by hitting
each company's ATS API directly.

ATS platforms covered:
  - Phenom People  → ABB  (careers.abb)
  - Workday        → Rockwell Automation, Yokogawa
  - Oracle HCM     → Honeywell, Emerson
  - SAP/Siemens    → Siemens (jobs.siemens.com REST API)
"""

import re
import time
import uuid
import requests
from bs4 import BeautifulSoup
from datetime import date

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

MENA_KEYWORDS = [
    "egypt", "cairo", "alexandria",
    "saudi", "ksa", "riyadh", "jeddah",
    "uae", "dubai", "abu dhabi", "united arab emirates",
    "qatar", "doha",
    "kuwait",
    "bahrain", "manama",
    "oman", "muscat",
    "jordan", "amman",
    "iraq", "baghdad",
    "lebanon", "beirut",
    "libya", "tunisia", "algeria", "morocco",
    "middle east", "mena", "gcc", "north africa",
]

CONTROL_KEYWORDS = [
    "automation", "control", "instrumentation", "plc", "scada",
    "dcs", "process control", "instrument", "commissioning",
    "field engineer", "system engineer", "ots", "hmi",
]


def is_mena(location_text: str) -> bool:
    t = location_text.lower()
    return any(k in t for k in MENA_KEYWORDS)


def is_relevant_role(title: str) -> bool:
    t = title.lower()
    return any(k in t for k in CONTROL_KEYWORDS)


def normalize(company, title, location, description, url, source, job_id=None):
    return {
        "id": job_id or url,
        "title": title,
        "company": company,
        "location": location,
        "description": (description or "")[:3000],
        "job_url": url,
        "date_posted": "",
        "date_found": date.today().isoformat(),
        "source": source,
    }


# ─────────────────────────────────────────────────────────────────
# 1. ABB  —  Phenom People /widgets API
# ─────────────────────────────────────────────────────────────────

ABB_DOMAIN = "careers.abb"
ABB_REF_NUM = "ABB1GLOBAL"   # confirmed from page HTML

def scrape_abb(search_terms=None):
    """Fetch all jobs from ABB's Phenom People career site and filter for MENA + control roles."""
    print("\n[ABB] Phenom People API …")
    jobs_found = []
    page = 0
    size = 50

    while True:
        try:
            payload = {
                "lang": "en_global",
                "deviceType": "desktop",
                "country": "global",
                "pageName": "search-results",
                "size": size,
                "from": page * size,
                "jobs": True,
                "counts": True,
                "all_fields": ["category", "country", "city", "type"],
                "clearAll": False,
                "jdsource": "facets",
                "isSliderEnable": False,
                "pageId": "page20",
                "siteType": "external",
                "keywords": "",
                "global": True,
                "selected_fields": {},
                "sort": {"order": "desc", "field": "postedDate"},
                "locationData": {},
                "refNum": ABB_REF_NUM,
                "ddoKey": "refineSearch",
            }
            resp = requests.post(
                f"https://{ABB_DOMAIN}/widgets",
                json=payload,
                headers={**HEADERS, "Content-Type": "application/json"},
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
                    job_url = f"https://{ABB_DOMAIN}/global/en/job/{seq}/{title.lower().replace(' ', '-')}"
                    jobs_found.append(normalize(
                        company="ABB",
                        title=title,
                        location=location,
                        description=job.get("descriptionTeaser", ""),
                        url=job_url,
                        source="ABB Careers",
                        job_id=f"abb_{seq}",
                    ))

            print(f"  Page {page}: {len(jobs_page)} jobs | total {total} | MENA hits so far: {len(jobs_found)}")

            if (page + 1) * size >= total:
                break
            page += 1
            time.sleep(0.8)

        except Exception as e:
            print(f"  [ABB ERROR] {e}")
            break

    print(f"  → {len(jobs_found)} MENA automation jobs from ABB")
    return jobs_found


# ─────────────────────────────────────────────────────────────────
# 2. WORKDAY  —  Rockwell Automation + Yokogawa
# ─────────────────────────────────────────────────────────────────

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


def _workday_fetch_page(tenant, site, wd, offset=0, limit=50, search_text=""):
    url = f"https://{tenant}.{wd}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs"
    payload = {
        "appliedFacets": {},
        "limit": limit,
        "offset": offset,
        "searchText": search_text,
    }
    resp = requests.post(
        url,
        json=payload,
        headers={
            **HEADERS,
            "Content-Type": "application/json",
            "Referer": f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}",
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def scrape_workday_company(cfg):
    company = cfg["company"]
    tenant = cfg["tenant"]
    site = cfg["site"]
    wd = cfg["wd"]
    print(f"\n[{company}] Workday CXS API …")
    jobs_found = []
    offset = 0
    limit = 50

    while True:
        try:
            data = _workday_fetch_page(tenant, site, wd, offset=offset, limit=limit)
            postings = data.get("jobPostings", [])
            total = data.get("total", 0)

            if not postings:
                break

            for p in postings:
                title = p.get("title", "") or ""
                location = p.get("locationsText", "") or ""
                ext_path = p.get("externalPath", "")
                job_url = f"https://{tenant}.{wd}.myworkdayjobs.com/en-US/{site}{ext_path}"

                if is_mena(location) and is_relevant_role(title):
                    jobs_found.append(normalize(
                        company=company,
                        title=title,
                        location=location,
                        description=p.get("jobDescription", ""),
                        url=job_url,
                        source=f"{company} Careers (Workday)",
                        job_id=f"{tenant}_{ext_path}",
                    ))

            print(f"  offset {offset}: {len(postings)} | total {total} | MENA hits: {len(jobs_found)}")

            if offset + limit >= total:
                break
            offset += limit
            time.sleep(1.2)

        except Exception as e:
            print(f"  [{company} ERROR] {e}")
            break

    print(f"  → {len(jobs_found)} MENA automation jobs from {company}")
    return jobs_found


def scrape_all_workday():
    results = []
    for cfg in WORKDAY_COMPANIES:
        results.extend(scrape_workday_company(cfg))
    return results


# ─────────────────────────────────────────────────────────────────
# 3. ORACLE HCM  —  Honeywell + Emerson
# ─────────────────────────────────────────────────────────────────

ORACLE_COMPANIES = [
    {
        "company": "Honeywell",
        "domain": "ibqbjb.fa.ocs.oraclecloud.com",
        "site_number": "CX_1",        # from the URL you gave
    },
    {
        "company": "Emerson",
        "domain": "hdjq.fa.us2.oraclecloud.com",
        "site_number": "CX_1",        # from the URL you gave
    },
]


def _oracle_fetch_page(domain, site_number, offset=0, limit=100):
    url = f"https://{domain}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    params = {
        "onlyData": "true",
        "expand": "requisitionList.workLocation,requisitionList.secondaryLocations",
        "finder": f"findReqs;siteNumber={site_number}",
        "facetsList": "LOCATIONS;CATEGORIES;ORGANIZATIONS;POSTING_DATES",
        "limit": limit,
        "offset": offset,
    }
    uid = str(uuid.uuid4())
    resp = requests.get(
        url,
        params=params,
        headers={
            **HEADERS,
            "ora-irc-cx-userid": uid,
            "ora-irc-language": "en",
            "content-type": "application/vnd.oracle.adf.resourceitem+json;charset=utf-8",
        },
        timeout=25,
    )
    resp.raise_for_status()
    return resp.json()


def _oracle_site_url(domain, site_number, job_id):
    # Public-facing URL for a given job
    return f"https://{domain}/hcmUI/CandidateExperience/en/sites/{site_number}/job/{job_id}"


def scrape_oracle_company(cfg):
    company = cfg["company"]
    domain = cfg["domain"]
    site_number = cfg["site_number"]
    print(f"\n[{company}] Oracle HCM API …")
    jobs_found = []
    offset = 0
    limit = 100

    while True:
        try:
            data = _oracle_fetch_page(domain, site_number, offset, limit)
            items = data.get("items", [])
            if not items:
                break

            total = items[0].get("TotalJobsCount", 0)
            req_list = items[0].get("requisitionList", [])

            if not req_list:
                break

            for job in req_list:
                title = job.get("Title", "") or ""
                location = job.get("PrimaryLocation", "") or ""
                country = job.get("PrimaryLocationCountry", "") or ""
                combined_loc = f"{location} {country}"
                job_id = str(job.get("Id", ""))
                job_url = _oracle_site_url(domain, site_number, job_id)
                desc = job.get("ShortDescriptionStr", "") or ""

                if is_mena(combined_loc) and is_relevant_role(title):
                    jobs_found.append(normalize(
                        company=company,
                        title=title,
                        location=combined_loc.strip(),
                        description=desc,
                        url=job_url,
                        source=f"{company} Careers (Oracle HCM)",
                        job_id=f"{company.lower()}_{job_id}",
                    ))

            print(f"  offset {offset}: {len(req_list)} | total {total} | MENA hits: {len(jobs_found)}")

            has_more = data.get("hasMore", False)
            if not has_more or offset + limit >= total:
                break
            offset += limit
            time.sleep(1.0)

        except Exception as e:
            print(f"  [{company} ERROR] {e}")
            break

    print(f"  → {len(jobs_found)} MENA automation jobs from {company}")
    return jobs_found


def scrape_all_oracle():
    results = []
    for cfg in ORACLE_COMPANIES:
        results.extend(scrape_oracle_company(cfg))
    return results


# ─────────────────────────────────────────────────────────────────
# 4. SIEMENS  —  jobs.siemens.com REST API
# ─────────────────────────────────────────────────────────────────

SIEMENS_MENA_COUNTRIES = [
    "Egypt", "Saudi Arabia", "United Arab Emirates", "Qatar",
    "Kuwait", "Bahrain", "Oman", "Jordan", "Iraq", "Morocco",
]

def scrape_siemens():
    """Siemens uses an Ashby-like REST API at jobs.siemens.com."""
    print("\n[Siemens] jobs.siemens.com REST API …")
    jobs_found = []

    for country in SIEMENS_MENA_COUNTRIES:
        try:
            # Siemens job search endpoint (confirmed from XHR inspection)
            url = "https://jobs.siemens.com/api/apply/v2/jobs"
            params = {
                "domain": "siemens.com",
                "start": 0,
                "num": 100,
                "location": country,
                "pid": "",
                "trigger": "page_load",
                "sort_by": "relevance",
            }
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
            resp.raise_for_status()
            data = resp.json()

            positions = data.get("positions", [])
            for p in positions:
                title = p.get("name", "") or ""
                loc = p.get("location", "") or ""
                desc = p.get("t_update", "") or p.get("description", "") or ""
                job_url = p.get("canonicalPositionUrl", "") or f"https://jobs.siemens.com/jobs/{p.get('id','')}"

                if is_relevant_role(title):
                    jobs_found.append(normalize(
                        company="Siemens",
                        title=title,
                        location=loc,
                        description=BeautifulSoup(desc, "html.parser").get_text()[:3000] if desc else "",
                        url=job_url,
                        source="Siemens Careers",
                        job_id=f"siemens_{p.get('id','')}",
                    ))

            print(f"  {country}: {len(positions)} jobs | hits: {len([j for j in jobs_found if country.lower() in j['location'].lower()])}")
            time.sleep(0.5)

        except Exception as e:
            print(f"  [Siemens/{country} ERROR] {e}")

    print(f"  → {len(jobs_found)} MENA automation jobs from Siemens")
    return jobs_found


# ─────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────

def scrape_direct_sites():
    all_jobs = []
    all_jobs.extend(scrape_abb())
    all_jobs.extend(scrape_all_workday())
    all_jobs.extend(scrape_all_oracle())
    all_jobs.extend(scrape_siemens())
    print(f"\n✅ Total from direct sites: {len(all_jobs)} jobs")
    return all_jobs
