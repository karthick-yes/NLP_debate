import requests
import io
import pdfplumber
from urllib3.exceptions import InsecureRequestWarning
import warnings
from dotenv import load_dotenv
from langchain_text_splitters import RecursiveCharacterTextSplitter
import json
import time
import random
from typing import List, Dict, Any, Optional
from google import generativeai as genai
import google.api_core.exceptions
from pydantic import BaseModel
import os
import asyncio
import aiohttp  # Not directly used in the sync version but kept from original
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import argparse  # Import argparse
import re  # Import re for sanitizing filenames

load_dotenv()

warnings.filterwarnings("ignore", category=InsecureRequestWarning)


class LiteraryReference(BaseModel):
    poeti_reference_type: str
    parliamentarian: str
    date: str
    author: str
    quoted_text: str


class ParliamentaryReferenceExtractor:
    MAX_TOKEN_LEN = 200000

    def __init__(self, model_name="gemini-2.0-flash"):
        self.model_name = model_name
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://eparlib.nic.in",
        }
        # Configure the client with your API key
        genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
        self.client = genai.GenerativeModel(self.model_name)

        # Optimized rate limiting for 2000 RPM (33.33 RPS)
        # Use 30 RPS to leave buffer for retries
        self.requests_per_second = 30
        self.min_delay = 1.0 / self.requests_per_second  # ~0.033 seconds
        self.retry_attempts = 2  # Reduced retries since we have good rate

        # Thread-safe rate limiting
        self.last_request_time = 0
        self.rate_limit_lock = threading.Lock()

        self.TEMPLATE = """
            You are a poetic and literary reference analyzer for parliamentary debates. Extract all poetic devices, literary techniques, and creative language use from the following parliamentary debate transcript.

            TEXT: {text}
            
            PARLIAMENT SESSION INFO:
            - Lok Sabha Number: {lok_sabha_number}
            - Session Number: {session_number}
            - Date: {date}
            
            IDENTIFY AND EXTRACT:
            1. **Direct Poetry/Literature Quotes**: Actual poems, verses, or literary works quoted
            2. **Poetic Devices Used by Parliamentarians**: 
                - Alliteration (e.g., "blundering, plundering and thundering")
                - Rhyme schemes and rhythmic patterns
                - Metaphors and similes with poetic quality
                - Repetitive patterns for emphasis
            3. **Creative Literary Techniques**:
                - Wordplay and puns
                - Rhetorical devices with poetic flair
                - Rhythmic speech patterns
                - Creative compound phrases
            4. **References to Literary Works**: Mentions of books, authors, characters, or literary themes

            EXAMPLES OF WHAT TO CAPTURE:
            - "blundering, plundering and thundering" (alliteration with rhythmic pattern)
            - Direct quotes from poems or literature
            - Creative compound words or phrases with poetic quality
            - Rhythmic repetitions used for rhetorical effect

            For each reference found:
            - Use the session info provided above (do not extract date from text)
            - Identify the parliamentarian's name from the text format "Shri [Name]:" or similar
            - Classify the type of poetic/literary device or reference
            - Extract the exact text that demonstrates the technique

            If no poetic or literary references exist, return an empty JSON array.

            Return results in a valid JSON format as an array of objects where each object conforms to this schema:
            {{
                "poeti_reference_type": "...",
                "parliamentarian": "...",
                "date": "...",
                "author": "...",
                "quoted_text": "..."
            }}
            """

    def parse_filename_info(self, url: str) -> Dict[str, str]:
        """
        Parses the PDF URL to extract Lok Sabha number, session number, and date.
        This version is updated to handle multiple filename formats.
        """
        try:
            # Pattern 1: Tries to match format 'lsd_01_01_15-05-1952.pdf'
            # It looks for a full DD-MM-YYYY date. This is the most specific pattern.
            match = re.search(
                r"/(?:lsd|rsd|l_s|r_s)_(\d+)_(\d+)_(\d{2}-\d{2}-\d{4})",
                url,
                re.IGNORECASE,
            )
            if match:
                return {
                    "lok_sabha_number": match.group(1).lstrip(
                        "0"
                    ),  # Remove leading zeros
                    "session_number": match.group(2).lstrip(
                        "0"
                    ),  # Remove leading zeros
                    "date": match.group(3),  # Full date is captured
                }

            # Pattern 2: Tries to match format 'L_S_16_01_2014_...' as a fallback
            # It looks for a 4-digit year if the full date format isn't present.
            match = re.search(
                r"/(?:lsd|rsd|l_s|r_s)_(\d+)_(\d+)_(\d{4})", url, re.IGNORECASE
            )
            if match:
                return {
                    "lok_sabha_number": match.group(1).lstrip("0"),
                    "session_number": match.group(2).lstrip("0"),
                    "date": match.group(3),  # Only the year is available
                }

            # If neither pattern matches, then show the warning.
            print(f"Warning: Could not parse session info from URL: {url}")
            return {
                "lok_sabha_number": "Unknown",
                "session_number": "Unknown",
                "date": "Unknown",
            }
        except Exception as e:
            print(f"Error in parse_filename_info: {e}")
            return {
                "lok_sabha_number": "Unknown",
                "session_number": "Unknown",
                "date": "Unknown",
            }

    def rate_limited_delay(self):
        """Thread-safe rate limiting"""
        with self.rate_limit_lock:
            current_time = time.time()
            time_since_last = current_time - self.last_request_time
            if time_since_last < self.min_delay:
                sleep_time = self.min_delay - time_since_last
                time.sleep(sleep_time)
            self.last_request_time = time.time()

    def download_pdf(self, url: str) -> Optional[bytes]:
        max_retries = 2

        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    delay = 2 * attempt + random.uniform(0, 1)
                    print(
                        f"Retrying download attempt {attempt + 1}/{max_retries} after {delay:.1f}s delay..."
                    )
                    time.sleep(delay)

                session = requests.Session()
                enhanced_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept-Encoding": "gzip, deflate, br",
                    "DNT": "1",
                    "Connection": "keep-alive",
                    "Upgrade-Insecure-Requests": "1",
                    "Sec-Fetch-Dest": "document",
                    "Sec-Fetch-Mode": "navigate",
                    "Sec-Fetch-Site": "none",
                    "Cache-Control": "max-age=0",
                    "Referer": "https://eparlib.nic.in/",
                }
                session.headers.update(enhanced_headers)

                response = session.get(
                    url=url, verify=False, timeout=30, stream=True, allow_redirects=True
                )
                response.raise_for_status()

                content = b""
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        content += chunk

                session.close()
                print(f"Successfully downloaded PDF ({len(content)} bytes)")
                return content

            except Exception as e:
                print(f"Download error on attempt {attempt + 1}: {str(e)[:100]}...")
                if attempt == max_retries - 1:
                    print(f"Failed to download after {max_retries} attempts")
        return None

    def extract_text_from_pdf(self, pdf_content: bytes) -> str:
        text = ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
                for page in pdf.pages:
                    page_text = page.extract_text()
                    if page_text:
                        text += page_text + "\n\n"
        except Exception as e:
            print(f"Error extracting text from PDF: {e}")
        return text

    def extract_references_from_chunk(
        self, text_chunk: str, session_info: Dict[str, str], chunk_index: int = 0
    ) -> List[Dict[str, Any]]:
        full_prompt = self.TEMPLATE.format(
            text=text_chunk,
            lok_sabha_number=session_info["lok_sabha_number"],
            session_number=session_info["session_number"],
            date=session_info["date"],
        )

        for attempt in range(self.retry_attempts):
            try:
                self.rate_limited_delay()

                response = self.client.generate_content(
                    contents=full_prompt,
                    generation_config={
                        "response_mime_type": "application/json",
                    },
                )
                print(f"API Response for chunk {chunk_index + 1} received")
                return json.loads(response.text) if response.text else []

            except google.api_core.exceptions.ResourceExhausted as e:
                print(f"RATE LIMIT on chunk {chunk_index + 1} (attempt {attempt + 1})")
                if attempt < self.retry_attempts - 1:
                    time.sleep(2**attempt)
                else:
                    print(f"Failed chunk {chunk_index + 1} after retries")
                    return []

            except Exception as e:
                print(f"Error on chunk {chunk_index + 1} (attempt {attempt + 1}): {e}")
                if attempt == self.retry_attempts - 1:
                    return []
                time.sleep(1)
        return []

    def extract_references(
        self, text: str, session_info: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        splitter = RecursiveCharacterTextSplitter(chunk_size=20000, chunk_overlap=500)
        chunks = splitter.split_text(text)

        print(f"Split into {len(chunks)} chunks")

        all_results = []
        with ThreadPoolExecutor(max_workers=10) as executor:
            future_to_chunk = {
                executor.submit(
                    self.extract_references_from_chunk, chunk, session_info, i
                ): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(future_to_chunk):
                chunk_idx = future_to_chunk[future]
                try:
                    chunk_results = future.result()
                    if chunk_results:
                        all_results.extend(chunk_results)
                    print(
                        f"Chunk {chunk_idx + 1}: {len(chunk_results) if chunk_results else 0} references"
                    )
                except Exception as e:
                    print(f"Chunk {chunk_idx + 1} failed: {e}")
        return all_results

    def process_pdf_url(self, url: str) -> List[Dict[str, Any]]:
        print(f"Processing: {url}")

        session_info = self.parse_filename_info(url)

        pdf_content = self.download_pdf(url)
        if not pdf_content:
            return []

        text = self.extract_text_from_pdf(pdf_content)
        if not text:
            return []

        return self.extract_references(text, session_info)

    def save_results_immediately(
        self, url: str, references: List[Dict[str, Any]], output_filepath: str
    ):
        existing_results = {}
        if os.path.exists(output_filepath):
            try:
                with open(output_filepath, "r", encoding="utf-8") as f:
                    file_content = f.read()
                    if file_content:
                        existing_results = json.loads(file_content)
            except (json.JSONDecodeError, IOError) as e:
                print(f"Could not load existing results from {output_filepath}: {e}")

        existing_results[url] = references

        try:
            with open(output_filepath, "w", encoding="utf-8") as f:
                json.dump(existing_results, f, indent=2, ensure_ascii=False)
            print(f"Results saved for {url} to {output_filepath}")
        except Exception as e:
            print(f"Error saving results to {output_filepath}: {e}")


def sanitize_filename_from_url(url: str) -> str:
    """Sanitizes a URL to create a safe and consistent filename."""
    filename_base = re.sub(r"https?://", "", url)
    filename_base = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename_base).strip("_.-")
    return filename_base[:100]


def main():
    parser = argparse.ArgumentParser(
        description="Extract literary references from PDFs linked in a file."
    )
    parser.add_argument(
        "original_url_for_naming",
        type=str,
        help="The original starting URL. Used to find the correct 'pdf_links.txt' file and name the output JSON.",
    )
    args = parser.parse_args()

    extractor = ParliamentaryReferenceExtractor()

    # Use the consistent sanitization function
    sanitized_url_name = sanitize_filename_from_url(args.original_url_for_naming)

    # --- YOUR FIX (This part is correct and now works because the previous scripts are fixed) ---
    input_dir_for_pdf_links = os.path.join(
        "link_extractor_scripts", "extracted_pdf_urls"
    )
    input_pdf_links_filepath = os.path.join(
        input_dir_for_pdf_links, f"pdf_links_for_{sanitized_url_name}.txt"
    )

    output_json_dir = os.path.join("literary_references_output")  # Corrected path
    os.makedirs(output_json_dir, exist_ok=True)
    output_json_filepath = os.path.join(
        output_json_dir, f"literary_references_from_{sanitized_url_name}.json"
    )
    # --- FIX ENDS HERE ---

    try:
        with open(input_pdf_links_filepath, "r", encoding="utf-8") as f:
            urls = [line.strip() for line in f if line.strip()]
    except FileNotFoundError:
        print(f"Error: PDF links file '{input_pdf_links_filepath}' not found.")
        print(
            "Please ensure the 'pdf_link_extractor.py' script was run successfully with the same original URL."
        )
        return
    except Exception as e:
        print(f"Error reading PDF links file {input_pdf_links_filepath}: {e}")
        return

    print(f"Found {len(urls)} PDFs to process from: {input_pdf_links_filepath}")
    # ... (rest of the main function is correct) ...
    # Initialize output JSON file
    with open(output_json_filepath, "w", encoding="utf-8") as f:
        json.dump({}, f, indent=2, ensure_ascii=False)

    total_references = 0
    start_time = time.time()

    # Process PDFs sequentially
    for idx, url in enumerate(urls, 1):
        try:
            print(f"\n[{idx}/{len(urls)}] Processing: {url}")
            references = extractor.process_pdf_url(url)

            extractor.save_results_immediately(url, references, output_json_filepath)
            total_references += len(references)

            elapsed = time.time() - start_time
            avg_time = elapsed / idx if idx > 0 else 0
            eta = avg_time * (len(urls) - idx)

            print(f"Saved {len(references)} references | ETA: {eta / 60:.1f}min")

        except Exception as e:
            print(f"Failed to process {url}: {e}")
            extractor.save_results_immediately(url, [], output_json_filepath)

    end_time = time.time()
    total_time = end_time - start_time

    print(f"\nCOMPLETED!")
    print(f"Total references found: {total_references}")
    print(f"Total time: {total_time / 60:.1f} minutes")
    if urls:
        print(f"Average: {total_time / len(urls):.1f} seconds per PDF")
    print(f"Results saved to: {output_json_filepath}")


if __name__ == "__main__":
    main()
