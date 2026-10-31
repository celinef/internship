import os
import hashlib
from google import genai
import gspread
import requests
from bs4 import BeautifulSoup

# 1. Initialize Gemini Client (picks up GEMINI_API_KEY environment variable)
client = genai.Client()

# 2. Connect to Google Sheets for Dynamic Settings & Logging
gc = gspread.service_account(filename="service_account.json")
sheet = gc.open("Internship Tracker")

# Read user-adjustable criteria from the "Settings" tab
config_row = sheet.worksheet("Settings").get_all_records()[0]
target_years = config_row.get("Target Academic Years", "Freshman, Sophomore, Junior, Senior")
preferred_majors = config_row.get("Preferred Majors", "Business, Marketing, Finance")
expertise_keywords = config_row.get("Expertise Keywords", "SEO, social media, communications, financial analysis")
max_distance = config_row.get("Max Distance", "Anywhere in USA")

# Get existing listings to prevent duplicates
listings_ws = sheet.worksheet("Listings")
existing_hashes = set(listings_ws.col_values(7))  # Column 7: Hash

# 3. Target URLs to Scrape
TARGET_URLS = [
    "https://www.simplyhired.com/search?q=summer+intern&l=seattle%2C+wa",
    "https://simplify.jobs/l/Top-Internships-in-Seattle",
    "https://careers.uw.edu/jobs/?ctag%5B%5D=internships",
    "https://www.indeed.com/jobs?q=summer+internship&l=Seattle%2C+WA"
]

def fetch_raw_listings():
    """Simple scraper stub to extract basic job card text from URLs."""
    scraped_jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for url in TARGET_URLS:
        try:
            response = requests.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                # Generic extraction logic tailored to common job cards
                for card in soup.find_all(['div', 'article'], limit=15):
                    text = card.get_text(separator=" ", strip=True)
                    if len(text) > 50:  # Ignore empty containers
                        scraped_jobs.append({
                            "source_url": url,
                            "raw_text": text[:1500]  # Limit token size per snippet
                        })
        except Exception as e:
            print(f"Error scraping {url}: {e}")
            
    return scraped_jobs

def evaluate_with_gemini(job_text):
    """Uses Gemini 1.5 Flash to enforce hard and soft filters."""
    prompt = f"""
    You are an intelligent recruitment assistant. Evaluate this internship text against the criteria below.

    USER CRITERIA:
    1. HARD FILTER: Must be strictly an UNDERGRADUATE internship. (If graduate/PhD only, reject it).
    2. Academic Years (AND condition if specified): Must be open to: {target_years}
    3. Preferred Majors (OR condition): Must match at least one of: {preferred_majors}
    4. Areas of Expertise (OR condition): Must mention at least one of: {expertise_keywords}
    5. Location Radius: Must be located in: {max_distance} (Treat remote/anywhere in USA as valid).

    JOB DESCRIPTION TEXT:
    {job_text}

    Return your answer strictly in valid JSON format with these exact keys:
    - "is_match": true or false
    - "title": extracted job title string
    - "company": extracted company name string
    - "location": extracted location string
    - "reason": short 1-sentence explanation of why it fits or fails.
    """

    try:
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt,
        )
        # Clean text to parse JSON
        cleaned_response = response.text.strip().replace("```json", "").replace("```", "")
        import json
        return json.loads(cleaned_response)
    except Exception as e:
        print(f"Gemini evaluation error: {e}")
        return {"is_match": False}

def main():
    raw_jobs = fetch_raw_listings()
    new_rows = []

    for job in raw_jobs:
        # Evaluate via Gemini AI
        result = evaluate_with_gemini(job["raw_text"])
        
        if result.get("is_match", False):
            title = result.get("title", "Unknown Title")
            company = result.get("company", "Unknown Company")
            
            # Deduplication Check (Hash of Title + Company)
            job_hash = hashlib.md5(f"{title}-{company}".lower().encode()).hexdigest()
            
            if job_hash not in existing_hashes:
                existing_hashes.add(job_hash)
                new_rows.append([
                    str(os.date() if hasattr(os, 'date') else "2026-10-31"),
                    title,
                    company,
                    result.get("location", "Seattle, WA"),
                    job["source_url"],
                    result.get("reason", ""),
                    job_hash
                ])

    # Append matches to Google Sheet
    if new_rows:
        listings_ws.append_rows(new_rows)
        print(f"Successfully added {len(new_rows)} new filtered internships!")
    else:
        print("No new matching internships found today.")

if __name__ == "__main__":
    main()