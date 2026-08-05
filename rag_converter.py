import os
import json
import pandas as pd
from pathlib import Path

# ==========================
# CONFIGURATION
# ==========================

BASE_FOLDER = '/content/Benefix-main/Benefix-main'
OUTPUT_FILE = 'rag_data.jsonl'

def process_to_rag_jsonl():
    """
    Processes CSV database and README files into a JSONL format
    optimized for fast RAG model ingestion.
    """
    if not os.path.exists(BASE_FOLDER):
        print(f"Directory {BASE_FOLDER} not found.")
        return

    records_count = 0
    print(f"Starting conversion process...")

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
        csv_path = os.path.join(BASE_FOLDER, 'business_schemes.csv')
        if os.path.exists(csv_path):
            df = pd.read_csv(csv_path)
            for idx, row in df.iterrows():
                content = " | ".join([f"{col}: {val}" for col, val in row.items() if pd.notna(val)])
                record = {
                    "id": f"csv_row_{idx}",
                    "text": content,
                    "source": "business_schemes.csv",
                    "metadata": {"row": idx, "type": "database_record"}
                }
                outfile.write(json.dumps(record, ensure_ascii=False) + '\n')
                records_count += 1

        readme_path = os.path.join(BASE_FOLDER, 'README.md')
        if os.path.exists(readme_path):
            with open(readme_path, 'r', encoding='utf-8') as f:
                text = f.read()
                record = {
                    "id": "readme_content",
                    "text": text.strip(),
                    "source": "README.md",
                    "metadata": {"type": "documentation"}
                }
                outfile.write(json.dumps(record, ensure_ascii=False) + '\n')
                records_count += 1

    print(f"Successfully created {records_count} RAG chunks in {OUTPUT_FILE}.")

if __name__ == '__main__':
    process_to_rag_jsonl()
