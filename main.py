import os
import json
import hashlib
from google import genai
import requests
from bs4 import BeautifulSoup

# 1. Initialize Gemini Client
client = genai.Client()

# 2. Filter Criteria Configuration
CONFIG = {
    "target_years": "Freshman, Sophomore, Junior, Senior",
    "preferred_majors": "Business, Marketing, Finance",
    "expertise_keywords": "SEO, social media, communications, financial analysis",
    "max_distance": "Anywhere in USA"
}

# 3. Load Existing Web Data to Prevent Duplicates
DATA_FILE = "docs/jobs.json"
os.makedirs("docs", exist_ok=True)

if os.path.exists(DATA_FILE):
    with open(DATA_FILE, "r") as f:
        try:
            existing_jobs = json.load(f)
        except json.JSONDecodeError:
            existing_jobs = []
else:
    existing_jobs = []

existing_hashes = {job.get("hash") for job in existing_jobs if "hash" in job}

# 4. Target URLs to Scrape
TARGET_URLS = [
    "https://www.simplyhired.com/search?q=summer+intern&l=seattle%2C+wa",
    "https://simplify.jobs/l/Top-Internships-in-Seattle",
    "https://careers.uw.edu/jobs/?ctag%5B%5D=internships",
    "https://www.indeed.com/jobs?q=summer+internship&l=Seattle%2C+WA"
]

def fetch_raw_listings():
    scraped_jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for url in TARGET_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                for card in soup.find_all(['div', 'article'], limit=15):
                    text = card.get_text(separator=" ", strip=True)
                    if len(text) > 50:
                        scraped_jobs.append({
                            "source_url": url,
                            "raw_text": text[:1500]
                        })
        except Exception as e:
            print(f"Error scraping {url}: {e}")
            
    return scraped_jobs

def evaluate_with_gemini(job_text):
    prompt = f"""
    You are an intelligent recruitment assistant. Evaluate this internship text against the criteria below.

    USER CRITERIA:
    1. HARD FILTER: Must be strictly an UNDERGRADUATE internship. (If graduate/PhD only, reject it).
    2. Academic Years (AND condition): Open to: {CONFIG['target_years']}
    3. Preferred Majors (OR condition): Matches at least one: {CONFIG['preferred_majors']}
    4. Areas of Expertise (OR condition): Mentions at least one: {CONFIG['expertise_keywords']}
    5. Location Radius: Located in: {CONFIG['max_distance']} (Remote/USA valid).

    JOB DESCRIPTION TEXT:
    {job_text}

    Return strictly valid JSON with these keys:
    - "is_match": true or false
    - "title": extracted job title
    - "company": extracted company
    - "location": extracted location
    - "reason": short explanation of why it fits or fails.
    """

    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt,
        )
        cleaned = response.text.strip().replace("```json", "").replace("```", "")
        return json.loads(cleaned)
    except Exception as e:
        print(f"Gemini evaluation error: {e}")
        return {"is_match": False}

def main():
    raw_jobs = fetch_raw_listings()
    new_entries = []

    for job in raw_jobs:
        result = evaluate_with_gemini(job["raw_text"])
        
        if result.get("is_match", False):
            title = result.get("title", "Unknown Title")
            company = result.get("company", "Unknown Company")
            job_hash = hashlib.md5(f"{title}-{company}".lower().encode()).hexdigest()
            
            if job_hash not in existing_hashes:
                existing_hashes.add(job_hash)
                new_entries.append({
                    "date": "2026-10-31",
                    "title": title,
                    "company": company,
                    "location": result.get("location", "Seattle, WA"),
                    "url": job["source_url"],
                    "reason": result.get("reason", ""),
                    "hash": job_hash
                })

    if new_entries:
        all_jobs = new_entries + existing_jobs
        with open(DATA_FILE, "w") as f:
            json.dump(all_jobs, f, indent=2)
        print(f"Added {len(new_entries)} new internships to {DATA_FILE}!")
    else:
        print("No new matches found today.")

if __name__ == "__main__":
    main()
