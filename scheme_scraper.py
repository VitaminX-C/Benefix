import csv
import requests
import os
import sys
from bs4 import BeautifulSoup
import time
import urllib3

# Increase the CSV field size limit to handle massive blocks of scraped text
maxInt = sys.maxsize
while True:
    try:
        csv.field_size_limit(maxInt)
        break
    except OverflowError:
        maxInt = int(maxInt/10)

# Suppress the insecure request warnings since we are turning off SSL verification
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration ---
# Your script will now look for both Snigdha's and Sahana's exact files!
INPUT_CSV_FILES = ["business_schemes.csv", "student_schemes.csv"] 
OUTPUT_CSV_FILE = "scheme_data.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

def get_urls_from_csv(filename):
    """Reads a CSV file containing URLs and returns a list of URLs."""
    urls = []
    if os.path.exists(filename):
        try:
            # Using utf-8-sig in case the CSV has a byte-order mark (BOM)
            with open(filename, mode='r', encoding='utf-8-sig') as csv_file:
                reader = csv.DictReader(csv_file)
                for row in reader:
                    # Smart check: Looks for Snigdha's "Scheme URL" or falls back to "URL"
                    url = row.get('Scheme URL') or row.get('URL', '')
                    if url and isinstance(url, str):
                        url = url.strip()
                        urls.append(url)
        except Exception as e:
            print(f"Error reading CSV file '{filename}': {e}")
    else:
        print(f"Notice: Could not find the input file '{filename}'. Skipping.")
    return urls

def scrape_paragraphs_from_url(url):
    """
    Fetches the HTML from a URL and extracts the text from all <p> tags.
    Returns a single combined string of all paragraph text.
    """
    try:
        print(f"Scraping: {url}")
        # Added verify=False to bypass strict government SSL certificates
        response = requests.get(url, headers=HEADERS, timeout=10, verify=False)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        paragraphs = soup.find_all('p')
        
        text_content = []
        for p in paragraphs:
            text = p.get_text(strip=True)
            if text:
                text_content.append(text)
                
        combined_text = "\n\n".join(text_content)
        return combined_text
        
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return f"ERROR_FETCHING: {e}"
    except Exception as e:
        print(f"An unexpected error occurred processing {url}: {e}")
        return f"ERROR_PROCESSING: {e}"

def get_existing_urls(filename):
    """Reads the existing CSV and returns a set of URLs that have already been scraped."""
    existing_urls = set()
    if os.path.exists(filename):
        try:
            with open(filename, mode='r', encoding='utf-8') as csv_file:
                reader = csv.DictReader(csv_file)
                for row in reader:
                    if 'URL' in row:
                        existing_urls.add(row['URL'])
        except Exception as e:
            print(f"Could not read existing CSV: {e}")
    return existing_urls

def append_to_csv(data, filename):
    """Appends a list of dictionaries to a CSV file, creating it if it doesn't exist."""
    if not data:
        return

    print(f"\nAppending {len(data)} new records to {filename}...")
    file_exists = os.path.exists(filename)
    
    try:
        fieldnames = ['URL', 'Scraped_Text']
        with open(filename, mode='a', newline='', encoding='utf-8') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()
            for row in data:
                writer.writerow(row)
                
        print(f"Successfully updated {filename}")
    except Exception as e:
        print(f"Failed to update CSV: {e}")

def main():
    scraped_data = []
    target_urls = []
    
    # 1. Loop through all input files (Snigdha's, Sahana's, etc.)
    for input_file in INPUT_CSV_FILES:
        urls_from_file = get_urls_from_csv(input_file)
        target_urls.extend(urls_from_file)
        
    if not target_urls:
         print(f"Exiting because no URLs were found in {INPUT_CSV_FILES}.")
         return
         
    # Remove duplicates just in case Sahana and Snigdha found the same schemes
    target_urls = list(set(target_urls))
    print(f"Found {len(target_urls)} unique URLs to process.")

    # 2. Get the list of URLs we have already scraped (to avoid re-doing work)
    existing_urls = get_existing_urls(OUTPUT_CSV_FILE)
    print(f"Found {len(existing_urls)} URLs already in the master CSV.")
    
    for url in target_urls:
        if url in existing_urls:
            print(f"Skipping already scraped URL: {url}")
            continue

        # Skip PDFs since they are handled separately
        if '.pdf' in url.lower():
            print(f"Skipping PDF file: {url}")
            continue

        text = scrape_paragraphs_from_url(url)
        
        scraped_data.append({
            'URL': url,
            'Scraped_Text': text
        })
        
        time.sleep(1)
        
    if scraped_data:
        append_to_csv(scraped_data, OUTPUT_CSV_FILE)
    else:
        print("\nNo new URLs to scrape. The master CSV is already up to date!")

if __name__ == "__main__":
    print("Starting Web Scraper...")
    main()
    print("Done!")