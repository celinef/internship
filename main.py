import hashlib
import json
import os
from datetime import date
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from google import genai
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

CONFIG = {
    "target_years": "Freshman, Sophomore, Junior, Senior",
    "preferred_majors": "Business, Marketing, Finance",
    "expertise_keywords": "SEO, social media, communications, financial analysis",
    "max_distance": "Anywhere in USA",
}

DATA_DIR = Path("docs")
DATA_FILE = DATA_DIR / "jobs.json"

TARGET_URLS = [
    "https://www.simplyhired.com/search?q=summer+intern&l=seattle%2C+wa",
    "https://simplify.jobs/l/Top-Internships-in-Seattle",
    "https://careers.uw.edu/jobs/?ctag%5B%5D=internships",
    "https://www.indeed.com/jobs?q=summer+internship&l=Seattle%2C+WA",
]


def make_session():
    session = requests.Session()
    retries = Retry(
        total=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=None,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


SESSION = make_session()


def get_gemini_client():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Add it as a repository secret in GitHub Actions."
        )
    return genai.Client(api_key=api_key)


CLIENT = get_gemini_client()


def ensure_jobs_file():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not DATA_FILE.exists():
        with DATA_FILE.open("w", encoding="utf-8") as file:
            json.dump({"updated_on": None, "jobs": []}, file, indent=2)
        print(f"Created empty jobs file at {DATA_FILE}")


def load_existing_jobs():
    ensure_jobs_file()

    try:
        with DATA_FILE.open("r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        print("Existing jobs file invalid or unreadable; resetting it.")
        with DATA_FILE.open("w", encoding="utf-8") as file:
            json.dump({"updated_on": None, "jobs": []}, file, indent=2)
        return {"updated_on": None, "jobs": []}

    if isinstance(data, dict):
        if "jobs" not in data:
            data["jobs"] = []
        if "updated_on" not in data:
            data["updated_on"] = None
        return data

    # Backward compatibility for old format
    return {
        "updated_on": None,
        "jobs": data if isinstance(data, list) else [],
    }


def fetch_raw_listings():
    scraped_jobs = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    for url in TARGET_URLS:
        try:
            response = SESSION.get(url, headers=headers, timeout=15)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")

            cards = soup.find_all(["div", "article"], limit=20)
            for card in cards:
                text = card.get_text(separator=" ", strip=True)
                if len(text) > 50:
                    scraped_jobs.append({
                        "source_url": url,
                        "raw_text": text[:1500],
                    })
        except Exception as e:
            print(f"Error scraping {url}: {e}")

    return scraped_jobs


def extract_text_from_response(response):
    if hasattr(response, "text") and isinstance(response.text, str):
        text = response.text.strip()
        if text:
            return text

    if hasattr(response, "candidates") and response.candidates:
        candidate = response.candidates[0]
        if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
            parts = []
            for part in candidate.content.parts:
                if hasattr(part, "text"):
                    parts.append(part.text)
            if parts:
                return "".join(parts).strip()

    raise ValueError("Could not extract text from Gemini response.")


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
        response = CLIENT.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
        )
        cleaned = extract_text_from_response(response)
        if cleaned.startswith("```"):
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        return json.loads(cleaned)
    except Exception as e:
        print(f"Gemini evaluation error: {e}")
        return {"is_match": False}


def main():
    data = load_existing_jobs()
    existing_jobs = data.get("jobs", [])
    existing_hashes = {
        job.get("hash")
        for job in existing_jobs
        if isinstance(job, dict) and job.get("hash")
    }

    raw_jobs = fetch_raw_listings()
    new_entries = []

    for job in raw_jobs:
        result = evaluate_with_gemini(job["raw_text"])

        if not result.get("is_match", False):
            continue

        title = result.get("title", "Unknown Title")
        company = result.get("company", "Unknown Company")
        job_hash = hashlib.md5(f"{title}-{company}".lower().encode("utf-8")).hexdigest()

        if job_hash in existing_hashes:
            continue

        existing_hashes.add(job_hash)
        new_entries.append({
            "date": date.today().isoformat(),
            "title": title,
            "company": company,
            "location": result.get("location", "Seattle, WA"),
            "url": job["source_url"],
            "reason": result.get("reason", ""),
            "hash": job_hash,
        })

    if new_entries:
        all_jobs = new_entries + existing_jobs
        data["jobs"] = all_jobs
        data["updated_on"] = date.today().isoformat()
        with DATA_FILE.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        print(f"Added {len(new_entries)} new internships.")
    else:
        data["updated_on"] = date.today().isoformat()
        with DATA_FILE.open("w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
        print("No new matches found today. Job file preserved.")


if __name__ == "__main__":
    main()
