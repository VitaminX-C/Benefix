
import requests
from bs4 import BeautifulSoup
import csv
import os
import time
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def collect_schemes():
    # Extended list of potential Indian scholarship and scheme sources
    sources = [
        'https://www.india.gov.in/my-government/schemes',
        'https://scholarships.gov.in/',
        'https://www.buddy4study.com/scholarships',
        'https://www.vidyasaarathi.co.in/Vidyasaarathi/scholarship',
        'https://www.vidyalakshmi.co.in/Scholarship/',
        'https://www.drbrambedkarwelfaretrust.org/',
        'https://scholarships.up.gov.in/',
        'https://sje.rajasthan.gov.in/',
        'https://www.scholarships.net.in/',
        'https://digitalgujarat.gov.in/',
        'https://mahadbt.maharashtra.gov.in/',
        'https://e-kalyan.azg.gov.in/',
        'https://www.karnataka.gov.in/service/Scholarships',
        'https://esp.kerala.gov.in/',
        'https://www.tn.gov.in/scheme',
        'https://wbmdfcscholarship.org/',
        'https://scholarship.odisha.gov.in/',
        'https://www.pms.bih.nic.in/',
        'https://jnanabhumi.ap.gov.in/',
        'https://telangana.gov.in/Services/Scholarships',
        'https://dst.gov.in/scientific-programmes/scientific-engineering-research-board-serb',
        'https://www.ugc.gov.in/page/Scholarships-and-Fellowships.aspx',
        'https://www.aicte-india.org/schemes/students-development-schemes',
        'https://minorityaffairs.gov.in/en/schemesperformance/scholarship-schemes',
        'https://tribal.nic.in/Scholarship.aspx'
    ]
    
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    output_file = 'student_schemes.csv'
    
    existing_urls = set()
    if os.path.exists(output_file):
        with open(output_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                existing_urls.add(row['Scheme URL'])

    new_schemes = []
    keywords = ['scholarship', 'scheme', 'grant', 'fellowship', 'guidelines', 'beneficiary', 'eligibility']
    
    for url in sources:
        print(f"Processing: {url}")
        try:
            response = requests.get(url, headers=headers, timeout=10, verify=False)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')
            
            for link_tag in soup.find_all('a', href=True):
                text = link_tag.get_text(strip=True)
                href = link_tag['href']
                
                if any(k in text.lower() for k in keywords) and len(text) > 12:
                    full_url = href if href.startswith('http') else url.rstrip('/') + '/' + href.lstrip('/')
                    
                    if full_url not in existing_urls:
                        provider = url.split('//')[-1].split('/')[0].replace('www.', '')
                        new_schemes.append({
                            "Scheme Name": text,
                            "Scheme URL": full_url,
                            "Offered By": provider
                        })
                        existing_urls.add(full_url)
            time.sleep(1) # Polite delay
        except Exception as e:
            print(f"Skipping {url}: {e}")
            continue

    if new_schemes:
        file_exists = os.path.isfile(output_file)
        with open(output_file, 'a', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=["Scheme Name", "Scheme URL", "Offered By"])
            if not file_exists:
                writer.writeheader()
            writer.writerows(new_schemes)
        print(f"Successfully added {len(new_schemes)} new entries.")
    else:
        print("No new unique schemes found.")

if __name__ == '__main__':
    collect_schemes()
