#!/usr/bin/env python3
"""
AIListHub Automation Script
- Fetch tools from configured sources (ProductHunt, GitHub Trending, RSS feeds)
- Upsert into MySQL database
- Generate short SEO-friendly descriptions with OpenAI
- Download logos optionally and store local URLs
- Designed for cron execution (daily/nightly)
"""

import os
import re
import logging
import argparse
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from io import BytesIO
from dotenv import load_dotenv



# DB connector
import mysql.connector

# Load env
load_dotenv()
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_NAME = os.getenv("DB_NAME", "ai_listhub")
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
LOGO_DIR = os.getenv("LOGO_DOWNLOAD_DIR", "./logos")
USER_AGENT = os.getenv("USER_AGENT", "AIListHubBot/1.0")

headers = {"Authorization": "7w3eo0YVckXTS5hzztYhtPFT423eJ3_ZuzLtjZnVxj0"}
r = requests.get("https://api.producthunt.com/v2/api/graphql", headers=headers)


import google.generativeai as genai
import logging
import re

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")



load_dotenv()

genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

def generate_description_gemini(name, desc):
    prompt = f"""Write a concise 80-120 word SEO-friendly description for the AI tool below. 
    Use an engaging tone, mention the primary use-case, and include a 3-word tagline in parentheses.
    
    Tool Name: {name}
    Description: {desc}
    """

    try:
        model = genai.GenerativeModel("gemini-1.5-flash-latest")
        response = model.generate_content(prompt)
        return response.text.strip()
    except Exception as e:
        logging.error(f"Gemini generation failed: {e}")
        return None



logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Ensure logo dir exists
os.makedirs(LOGO_DIR, exist_ok=True)

def get_db_connection():
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        charset="utf8mb4"
    )

def slugify(s: str):
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9]+', '-', s)
    s = re.sub(r'-+', '-', s).strip('-')
    return s

def generate_description_gemini(name: str, category: str, url: str) -> str:
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        logging.warning("Gemini API key not found - skipping description generation")
        return ""

    try:
        genai.configure(api_key=GEMINI_API_KEY)

        prompt = f"""Write a concise 80-120 word SEO-friendly description for the AI tool below. 
Use an engaging tone, mention the primary use-case, and include a suggested 3-word tagline at the end in parentheses.
Tool name: {name}
Category: {category}
URL: {url}
Keep it human-readable and avoid marketing fluff."""

        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content(prompt)

        text = response.text.strip()
        text = re.sub(r'\n+', '\n', text)
        return text

    except Exception as e:
        logging.exception("Gemini generation failed: %s", e)
        return ""

def upsert_tool(record: dict, dry_run=False):
    UPSERT_SQL = """
INSERT INTO tools (name, url, category, description, logo_url, tags, source)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON DUPLICATE KEY UPDATE
    name=VALUES(name),
    category=VALUES(category),
    description=VALUES(description),
    logo_url=VALUES(logo_url),
    tags=VALUES(tags),
    source=VALUES(source),
    updated_on=NOW();
"""
    if dry_run:
        logging.info("[dry-run] Upsert: %s", record)
        return True
    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute(UPSERT_SQL, (
            record.get("name"),
            record.get("url"),
            record.get("category"),
            record.get("description"),
            record.get("logo_url"),
            record.get("tags"),
            record.get("source")
        ))
        conn.commit()
        cur.close()
        return True
    except Exception as e:
        logging.exception("DB upsert failed: %s", e)
        return False
    finally:
        conn.close()

def fetch_from_github_trending(language='python', since='daily', max_items=10):
    """
    Fetch trending GitHub repos using updated HTML selectors.
    """
    url = f"https://github.com/trending/{language}?since={since}"
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        repos = soup.find_all("article", class_="Box-row")[:max_items]

        results = []
        for repo in repos:
            # GitHub changed h1 -> h2 in trending repo titles
            h2_tag = repo.find("h2")
            if not h2_tag:
                continue
            a_tag = h2_tag.find("a")
            name = a_tag.get_text(strip=True).replace("\n", " ") if a_tag else ""
            link = urljoin("https://github.com", a_tag['href']) if a_tag else ""
            
            # Description may be in <p> tag
            description_tag = repo.find("p")
            desc = description_tag.get_text(strip=True) if description_tag else ""

            results.append({
                "name": name,
                "url": link,
                "category": "Open-source",
                "logo": "",
                "source": "github_trending",
                "short": desc
            })
        return results

    except Exception as e:
        logging.exception("GitHub trending fetch failed: %s", e)
        return []

def fetch_from_rss(feed_url, max_items=10):
    """
    Fetch RSS feed items using a real browser user-agent to avoid 403.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    }
    try:
        r = requests.get(feed_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, "xml")
        items = soup.find_all("item")[:max_items]

        results = []
        for item in items:
            title = item.title.get_text() if item.title else ""
            link = item.link.get_text() if item.link else ""
            desc = item.description.get_text() if item.description else ""
            results.append({
                "name": title,
                "url": link,
                "category": "Unknown",
                "logo": "",
                "source": feed_url,
                "short": desc
            })
        return results

    except requests.exceptions.HTTPError as http_err:
        logging.error("RSS fetch failed: %s", http_err)
        return []
    except Exception as e:
        logging.exception("RSS fetch failed: %s", e)
        return []


def process_candidate(cand: dict, generate_desc=True, dry_run=False):
    name = cand.get("name") or ""
    url = cand.get("url") or ""
    category = cand.get("category") or cand.get("short") or "Misc"
    logo = cand.get("logo") or ""
    source = cand.get("source") or "unknown"
    tags = cand.get("tags") or ""
    description = cand.get("description") or ""

    if not description and generate_desc:
        description = generate_description_gemini(name, category, url)

    record = {
        "name": name,
        "url": url,
        "category": category,
        "description": description,
        "logo_url": logo,
        "tags": tags,
        "source": source
    }
    return upsert_tool(record, dry_run=dry_run)

def main(argv=None):
    parser = argparse.ArgumentParser(description="AIListHub automation runner")
    parser.add_argument("--dry-run", action="store_true", help="Do not write to DB")
    parser.add_argument("--no-desc", action="store_true", help="Skip description generation")
    args = parser.parse_args(argv)

    dry_run = args.dry_run
    generate_desc = not args.no_desc

    gh_items = fetch_from_github_trending(language='python', since='daily', max_items=5)
    logging.info("Fetched %d GitHub trending items", len(gh_items))

    rss_items = fetch_from_rss("https://www.producthunt.com/feed", max_items=5)
    logging.info("Fetched %d RSS items", len(rss_items))

    candidates = []
    for lst in (gh_items, rss_items):
        candidates.extend(lst)

    seen = set()
    final = []
    for c in candidates:
        url = c.get("url", "").strip()
        if not url:
            continue
        if url in seen:
            continue
        seen.add(url)
        final.append(c)

    logging.info("Processing %d unique candidates", len(final))

    for cand in final:
        ok = process_candidate(cand, generate_desc=generate_desc, dry_run=dry_run)
        if ok:
            logging.info("Processed: %s", cand.get("name"))

if __name__ == "__main__":
    main()

from fastapi import FastAPI
app = FastAPI()

@app.get("/tools")
def list_tools():
    conn = get_db_connection()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM tools ORDER BY updated_on DESC LIMIT 50")
    data = cur.fetchall()
    cur.close()
    conn.close()
    return data

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=10000)
