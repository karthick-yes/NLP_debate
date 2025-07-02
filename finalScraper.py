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
from google import genai
from pydantic import BaseModel
import os

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
        self.client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY"))

        # Rate limiting parameters
        self.base_delay = 0.25  # Base delay between requests (seconds)
        self.max_delay = 60.0  # Maximum delay for exponential backoff
        self.retry_attempts = 3  # Number of retry attempts for failed requests

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

        If no poetic or literary references exist, return an empty array.

        Return results in JSON format as an array of objects with:
        literary_reference_type, parliamentarian, date, author, and quoted_text.
        """

    def parse_filename_info(self, url: str) -> Dict[str, str]:
        try:
            filename = (
                url.split("/")[-1].replace(".pdf", "").replace("#search=null%20", "")
            )
            parts = filename.split("_")
            if len(parts) >= 4:
                lok_sabha = parts[1]
                session = parts[2]
                date = parts[3]
                return {
                    "lok_sabha_number": lok_sabha,
                    "session_number": session,
                    "date": date,
                }
        except Exception as e:
            print(f"Error parsing filename: {e}")

        return {
            "lok_sabha_number": "Unknown",
            "session_number": "Unknown",
            "date": "Unknown",
        }

    def rate_limited_delay(self, chunk_index: int = 0):
        delay = self.base_delay + random.uniform(0.1, 0.5)

        # Progressive delay for later chunks (they tend to be less important)
        if chunk_index > 10:
            delay *= 1.5
        elif chunk_index > 5:
            delay *= 1.2

        print(f"Rate limiting: waiting {delay:.2f} seconds before next API call...")
        time.sleep(delay)

    def exponential_backoff_delay(self, attempt: int):
        delay = min(
            self.base_delay * (2**attempt) + random.uniform(0, 1), self.max_delay
        )
        print(
            f"Exponential backoff: attempt {attempt + 1}, waiting {delay:.2f} seconds..."
        )
        time.sleep(delay)

    def download_pdf(self, url: str) -> Optional[bytes]:
        try:
            response = requests.get(url=url, headers=self.headers, verify=False)
            response.raise_for_status()
            return response.content
        except Exception as e:
            print(f"Error downloading PDF from {url}: {e}")
            return None

    def extract_text_from_pdf(self, pdf_content: bytes) -> str:
        text = ""
        try:
            with pdfplumber.open(io.BytesIO(pdf_content)) as pdf:
                for page in pdf.pages:
                    page_width = page.width
                    page_height = page.height
                    left_bbox = (0, 0, page_width / 2, page_height)
                    right_bbox = (page_width / 2, 0, page_width, page_height)
                    left_text = page.within_bbox(left_bbox).extract_text() or ""
                    right_text = page.within_bbox(right_bbox).extract_text() or ""
                    text += left_text + "\n\n" + right_text + "\n\n"
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
                # Apply rate limiting before each API call
                if chunk_index > 0 or attempt > 0:
                    if attempt > 0:
                        self.exponential_backoff_delay(attempt)
                    else:
                        self.rate_limited_delay(chunk_index)

                response = self.client.models.generate_content(
                    model=self.model_name,
                    contents=full_prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_schema": list[LiteraryReference],
                    },
                )
                print(
                    f"API Response for chunk {chunk_index + 1}: {len(response.text)} characters"
                )
                return response.parsed if response.parsed else []

            except Exception as e:
                print(f"Error calling API (attempt {attempt + 1}): {e}")
                if attempt == self.retry_attempts - 1:
                    print(
                        f"Failed to process chunk {chunk_index + 1} after {self.retry_attempts} attempts"
                    )
                    return []
                # Exponential backoff will be applied on next iteration

    def extract_references(
        self, text: str, session_info: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        print("Using chunking strategy for better extraction accuracy")

        splitter = RecursiveCharacterTextSplitter(chunk_size=20000, chunk_overlap=1000)
        chunks = splitter.split_text(text)

        print(f"Split entire document into {len(chunks)} chunks for focused extraction")
        print(f"Estimated API calls: {len(chunks)} (with rate limiting)")

        # Process each chunk with rate limiting
        all_results = []
        for i, chunk in enumerate(chunks):
            print(f"Processing chunk {i + 1}/{len(chunks)}")
            chunk_results = self.extract_references_from_chunk(chunk, session_info, i)
            all_results.extend(chunk_results)
            print(f"Found {len(chunk_results)} references in chunk {i + 1}")

        return all_results

    def process_pdf_url(self, url: str) -> List[Dict[str, Any]]:
        print(f"Processing: {url}")

        # Parse session info from URL
        session_info = self.parse_filename_info(url)
        print(
            f"Session Info: Lok Sabha {session_info['lok_sabha_number']}, Session {session_info['session_number']}, Date {session_info['date']}"
        )

        pdf_content = self.download_pdf(url)
        if not pdf_content:
            return []

        text = self.extract_text_from_pdf(pdf_content)
        if not text:
            return []

        return self.extract_references(text, session_info)

    def save_results_immediately(
        self,
        url: str,
        references: List[Dict[str, Any]],
        output_file: str = "literary_references.json",
    ):
        # Load existing results if file exists
        existing_results = {}
        if os.path.exists(output_file):
            try:
                with open(output_file, "r", encoding="utf-8") as f:
                    existing_results = json.load(f)
            except Exception as e:
                print(f"Warning: Could not load existing results: {e}")

        # Convert Pydantic objects to dicts for JSON serialization
        serializable_references = []
        for ref in references:
            if hasattr(ref, "model_dump"):
                serializable_references.append(ref.model_dump())
            else:
                serializable_references.append(ref)

        # Add new results
        existing_results[url] = serializable_references

        # Save updated results
        try:
            with open(output_file, "w", encoding="utf-8") as f:
                json.dump(existing_results, f, indent=2, ensure_ascii=False)
            print(f"Results saved to {output_file}")
        except Exception as e:
            print(f"Error saving results: {e}")


def main():
    extractor = ParliamentaryReferenceExtractor()
    output_file = "literary_references.json"

    with open("scrape_results/pdf_links.txt", "r", encoding="utf-8") as f:
        urls = [line.strip() for line in f.readlines()]

    print(f"Processing {len(urls)} PDFs")
    start_time = time.time()

    # Initialize/clear the output file
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump({}, f)
    print(f"Initialized output file: {output_file}")

    for idx, url in enumerate(urls):
        print(f"\n{'=' * 60}")
        print(f"Processing PDF {idx + 1}/{len(urls)}: {url}")
        print(f"{'=' * 60}")

        references = extractor.process_pdf_url(url)

        # Save results immediately after processing each PDF
        extractor.save_results_immediately(url, references, output_file)

        print(f"\nFound {len(references)} literary references in {url}")
        for ref in references:
            if hasattr(ref, "model_dump"):
                print(json.dumps(ref.model_dump(), indent=2))
            else:
                print(json.dumps(ref, indent=2))
        print("\n" + "-" * 50 + "\n")

    end_time = time.time()
    print(f"Total processing time: {end_time - start_time:.2f} seconds")
    print(f"All results have been saved progressively to {output_file}")


if __name__ == "__main__":
    main()
