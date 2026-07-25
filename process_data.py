import json
import re

def clean_text(text):
    if not text: return ""
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'(?i)pursuant to section \d+[a-z]?', '', text)
    return text

def process_file(input_path, output_path):
    with open(input_path, 'r') as f, open(output_path, 'w') as out:
        for line in f:
            line = line.strip()
            if not line: continue
            try:
                record = json.loads(line)
                text = record.get('text', record.get('content', ''))
                record['cleaned_text'] = clean_text(text)
                out.write(json.dumps(record) + '\n')
            except json.JSONDecodeError:
                continue

if __name__ == '__main__':
    process_file('/content/benefix_schemes_dataset.jsonl', '/content/processed_data.jsonl')