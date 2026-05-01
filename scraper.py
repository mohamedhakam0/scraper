"""
MENA Automation & Control Engineer Job Tracker  v2
───────────────────────────────────────────────────
Sources:
  A) Job boards (via JobSpy)
     • LinkedIn, Indeed, Bayt, Wuzzuf (Google search)
  B) Direct company career sites
     • ABB          → Phenom People API
     • Honeywell    → Oracle HCM API
     • Emerson      → Oracle HCM API
     • Rockwell     → Workday CXS API
     • Yokogawa     → Workday CXS API
     • Siemens      → jobs.siemens.com REST API

Schedule: runs daily via GitHub Actions (see .github/workflows/scrape_jobs.yml)
Storage:  SQLite database  →  jobs.db
Output:   Searchable HTML  →  jobs_report.html
"""

import sqlite3
import os
from datetime import date, datetime
import pandas as pd

# Import our direct site scrapers
from scrapers.direct_sites import scrape_direct_sites

# ─────────────────────────────────────────────
# CONFIG
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

DB_PATH = "jobs.db"
HTML_PATH = "jobs_report.html"


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
                date.today().isoformat(),
                str(job.get("source", "")),
            ))
            if conn.execute("SELECT changes()").fetchone()[0] > 0:
                new_count += 1
        except Exception as e:
            print(f"  [DB ERROR] {e}")
    conn.commit()
    return new_count


# ─────────────────────────────────────────────
# JOB BOARD SCRAPER  (LinkedIn / Indeed / Bayt / Wuzzuf)
# ─────────────────────────────────────────────

def is_mena(row) -> bool:
    text = " ".join([
        str(row.get("location", "")),
        str(row.get("country", "")),
        str(row.get("city", "")),
        str(row.get("description", ""))[:500],
    ]).lower()
    return any(kw in text for kw in MENA_KEYWORDS)


def is_target_company(row) -> bool:
    company = str(row.get("company", "")).lower()
    return any(c.lower() in company for c in TARGET_COMPANIES)


def scrape_job_boards():
    """Scrape LinkedIn, Indeed, Bayt using JobSpy."""
    try:
        from jobspy import scrape_jobs
    except ImportError:
        print("  [WARNING] python-jobspy not installed, skipping job boards")
        return []

    all_results = []

    for term in SEARCH_TERMS:
        print(f"\n  🔍 JobSpy: '{term}'")
        try:
            jobs = scrape_jobs(
                site_name=["linkedin", "indeed", "bayt"],
                search_term=term,
                results_wanted=40,
                hours_old=72,
                description_format="markdown",
            )
            if jobs is not None and not jobs.empty:
                print(f"     raw: {len(jobs)}")
                all_results.append(jobs)
        except Exception as e:
            print(f"     [ERROR] {e}")

    if not all_results:
        return []

    combined = pd.concat(all_results, ignore_index=True)

    # Also add Wuzzuf via Google search term
    try:
        wuzzuf_results = scrape_jobs(
            site_name=["google"],
            google_search_term="automation control engineer jobs Egypt Wuzzuf site:wuzzuf.net",
            results_wanted=20,
            hours_old=72,
        )
        if wuzzuf_results is not None and not wuzzuf_results.empty:
            combined = pd.concat([combined, wuzzuf_results], ignore_index=True)
            print(f"  Wuzzuf (Google): {len(wuzzuf_results)} raw results")
    except Exception as e:
        print(f"  [Wuzzuf ERROR] {e}")

    # Filter
    combined = combined[combined.apply(is_target_company, axis=1)]
    combined = combined[combined.apply(is_mena, axis=1)]

    # Convert to dicts
    results = []
    for _, row in combined.iterrows():
        results.append({
            "id": str(row.get("id", row.get("job_url", "")))[:200],
            "title": str(row.get("title", "")),
            "company": str(row.get("company", "")),
            "location": f"{row.get('city','')} {row.get('state','')} {row.get('country','')}".strip(),
            "job_type": str(row.get("job_type", "")),
            "description": str(row.get("description", ""))[:3000],
            "job_url": str(row.get("job_url", "")),
            "date_posted": str(row.get("date_posted", "")),
            "date_found": date.today().isoformat(),
            "source": str(row.get("site", "jobspy")),
        })

    print(f"\n  Job boards total (after filters): {len(results)}")
    return results


# ─────────────────────────────────────────────
# HTML REPORT
# ─────────────────────────────────────────────

def generate_html(conn):
    rows = conn.execute("""
        SELECT title, company, location, job_type, date_posted, date_found,
               job_url, description, source
        FROM jobs
        ORDER BY date_found DESC, date_posted DESC
        LIMIT 600
    """).fetchall()

    source_counts = {}
    for r in rows:
        src = r[8] or "unknown"
        source_counts[src] = source_counts.get(src, 0) + 1

    company_counts = {}
    for r in rows:
        c = r[1] or "unknown"
        company_counts[c] = company_counts.get(c, 0) + 1

    cards = ""
    for r in rows:
        title, company, location, job_type, date_posted, date_found, url, desc, source = r
        desc_clean = (desc or "").replace("<", "&lt;").replace(">", "&gt;")
        desc_preview = desc_clean[:450]
        cards += f"""
        <div class="card" data-company="{company}" data-source="{source}">
            <div class="card-header">
                <div>
                    <span class="badge">{company}</span>
                    <span class="badge source">{source or ''}</span>
                </div>
                <span class="date">Found: {date_found}</span>
            </div>
            <h3><a href="{url}" target="_blank" rel="noopener">{title}</a></h3>
            <div class="meta">
                <span>📍 {location}</span>
                {"<span>🗓 " + date_posted + "</span>" if date_posted and date_posted != "None" else ""}
                {"<span>💼 " + job_type + "</span>" if job_type and job_type not in ("None","") else ""}
            </div>
            <p class="desc">{desc_preview}{"…" if len(desc_clean) > 450 else ""}</p>
            <a class="apply-btn" href="{url}" target="_blank" rel="noopener">View &amp; Apply →</a>
        </div>"""

    total_db = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]

    # Build source summary pills
    source_pills = "".join(
        f'<span class="pill" onclick="filterSource(\'{s}\')">{s} <b>{n}</b></span>'
        for s, n in sorted(source_counts.items(), key=lambda x: -x[1])
    )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>MENA Automation Jobs Tracker</title>
<style>
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{font-family:'Segoe UI',sans-serif;background:#0f1117;color:#e0e0e0;padding:20px}}
  h1{{color:#00c8ff;margin-bottom:4px;font-size:24px}}
  .subtitle{{color:#888;margin-bottom:20px;font-size:13px}}
  .stats{{background:#1a1d27;border-radius:10px;padding:14px 20px;margin-bottom:18px;
           display:flex;gap:30px;flex-wrap:wrap;border:1px solid #2a2d3a}}
  .stat{{display:flex;flex-direction:column}}
  .stat-val{{font-size:26px;font-weight:700;color:#00c8ff}}
  .stat-label{{font-size:12px;color:#888}}
  .pills{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}}
  .pill{{background:#1a1d27;border:1px solid #2a2d3a;color:#ccc;padding:5px 12px;
          border-radius:20px;font-size:12px;cursor:pointer;transition:border-color 0.2s}}
  .pill:hover,.pill.active{{border-color:#00c8ff;color:#00c8ff}}
  .search-bar{{width:100%;padding:10px 16px;border-radius:8px;border:1px solid #2a2d3a;
               background:#1a1d27;color:#e0e0e0;font-size:15px;margin-bottom:18px}}
  .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(400px,1fr));gap:14px}}
  .card{{background:#1a1d27;border:1px solid #2a2d3a;border-radius:12px;padding:16px;
          transition:border-color 0.2s}}
  .card:hover{{border-color:#00c8ff}}
  .card-header{{display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:10px}}
  .badge{{background:#00c8ff22;color:#00c8ff;padding:3px 9px;border-radius:20px;
           font-size:11px;font-weight:600;margin-right:5px;white-space:nowrap}}
  .badge.source{{background:#ffffff11;color:#aaa}}
  .date{{font-size:11px;color:#555;white-space:nowrap;padding-top:2px}}
  h3 a{{color:#e0e0e0;text-decoration:none;font-size:15px;line-height:1.4}}
  h3 a:hover{{color:#00c8ff}}
  .meta{{display:flex;gap:12px;margin:8px 0;font-size:12px;color:#888;flex-wrap:wrap}}
  .desc{{font-size:12px;color:#aaa;line-height:1.6;margin:8px 0;max-height:100px;overflow:hidden}}
  .apply-btn{{display:inline-block;margin-top:10px;padding:6px 14px;background:#00c8ff22;
               color:#00c8ff;border-radius:6px;text-decoration:none;font-size:12px;
               border:1px solid #00c8ff44;transition:background 0.2s}}
  .apply-btn:hover{{background:#00c8ff44}}
  .hidden{{display:none!important}}
  .updated{{text-align:right;font-size:11px;color:#444;margin-top:24px}}
  #count{{color:#888;font-size:13px;margin-bottom:12px}}
</style>
</head>
<body>
<h1>🌍 MENA Automation &amp; Control Engineer Jobs</h1>
<p class="subtitle">
  Auto-updated daily · Sources: ABB Careers, Honeywell Careers, Emerson Careers,
  Rockwell Careers, Yokogawa Careers, Siemens Careers, LinkedIn, Indeed, Bayt, Wuzzuf
</p>

<div class="stats">
  <div class="stat"><span class="stat-val">{total_db}</span><span class="stat-label">Total Tracked</span></div>
  <div class="stat"><span class="stat-val">{len(rows)}</span><span class="stat-label">Showing</span></div>
  <div class="stat"><span class="stat-val">{datetime.now().strftime('%b %d')}</span><span class="stat-label">Last Updated</span></div>
</div>

<div class="pills" id="source-pills">
  <span class="pill active" onclick="filterSource(null)">All sources</span>
  {source_pills}
</div>

<input class="search-bar" id="search" type="text"
       placeholder="Search title, company, location…"
       oninput="filterCards()">

<p id="count"></p>
<div class="grid" id="grid">{cards}</div>

<p class="updated">Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}</p>

<script>
let activeSource = null;

function filterSource(src) {{
  activeSource = src;
  document.querySelectorAll('.pill').forEach(p => p.classList.remove('active'));
  event.target.classList.add('active');
  filterCards();
}}

function filterCards() {{
  const q = document.getElementById('search').value.toLowerCase();
  const cards = document.querySelectorAll('.card');
  let visible = 0;
  cards.forEach(c => {{
    const matchQ = !q || c.innerText.toLowerCase().includes(q);
    const matchS = !activeSource || c.dataset.source === activeSource;
    if (matchQ && matchS) {{ c.classList.remove('hidden'); visible++; }}
    else {{ c.classList.add('hidden'); }}
  }});
  document.getElementById('count').textContent = visible + ' job' + (visible !== 1 ? 's' : '') + ' shown';
}}
filterCards();
</script>
</body>
</html>"""

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"📄 HTML report saved → {HTML_PATH}")


# ─────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  MENA Automation Job Tracker  v2")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 60)

    conn = init_db()
    total_new = 0

    # ── A. Direct company career sites ──────────────────────────
    print("\n── DIRECT CAREER SITES ────────────────────────────────")
    direct_jobs = scrape_direct_sites()
    new_direct = save_jobs(conn, direct_jobs)
    total_new += new_direct
    print(f"\n  Saved {new_direct} new jobs from direct sites")

    # ── B. Job boards via JobSpy ─────────────────────────────────
    print("\n── JOB BOARDS (LinkedIn / Indeed / Bayt / Wuzzuf) ─────")
    board_jobs = scrape_job_boards()
    new_boards = save_jobs(conn, board_jobs)
    total_new += new_boards
    print(f"\n  Saved {new_boards} new jobs from job boards")

    # ── Report ──────────────────────────────────────────────────
    total_db = conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
    print(f"\n{'─'*60}")
    print(f"  ✅ New jobs this run : {total_new}")
    print(f"  📦 Total in database : {total_db}")
    print(f"{'─'*60}")

    generate_html(conn)
    conn.close()
    print("\n🏁 Done.")
