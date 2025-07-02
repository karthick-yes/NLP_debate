# pdf_page_link_extractor_v2.py

import json
import asyncio
from urllib.parse import urljoin
import argparse
import re
import os  # <-- CHANGE: Import the os module for path handling

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, CacheMode
from crawl4ai.extraction_strategy import JsonCssExtractionStrategy


async def extract_all_links(start_url: str):
    # --- CHANGE: Standardize filename generation and output path ---
    # This logic now matches the other scripts to ensure consistency.
    # 1. Remove protocol (http://)
    # 2. Replace invalid characters with '_'
    # 3. Limit length to 100 characters
    filename_base = re.sub(r"https?://", "", start_url)
    filename_base = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename_base).strip("_.-")
    sanitized_name = filename_base[:100]

    # Define the correct output directory and ensure it exists
    output_dir = os.path.join("link_extractor_scripts", "extracted_links")
    os.makedirs(output_dir, exist_ok=True)

    # Construct the full path for the output file
    output_filename = os.path.join(output_dir, f"{sanitized_name}.txt")
    # --- END CHANGE ---

    # 1. Schema to extract table links
    schema = {
        "name": "Table Links",
        "baseSelector": "tr",
        "fields": [
            {
                "name": "link",
                "selector": 'td[headers="t4"] a',
                "type": "attribute",
                "attribute": "href",
            }
        ],
    }

    # 2. Config for table rows
    table_config = CrawlerRunConfig(
        extraction_strategy=JsonCssExtractionStrategy(schema, verbose=True),
        cache_mode=CacheMode.BYPASS,
    )

    # 3. Schema to extract just the next page href
    next_button_schema = {
        "name": "Next Page",
        "baseSelector": "div",
        "fields": [
            {
                "name": "next",
                "selector": "a.pull-right",
                "type": "attribute",
                "attribute": "href",
            }
        ],
    }

    next_button_config = CrawlerRunConfig(
        extraction_strategy=JsonCssExtractionStrategy(next_button_schema, verbose=True),
        cache_mode=CacheMode.BYPASS,
    )

    base_url = "https://eparlib.nic.in"
    current_url = start_url

    async with AsyncWebCrawler(verbose=True) as crawler:
        page_count = 0
        while current_url:
            print(f"Crawling page: {current_url}")

            # Extract links from table
            result = await crawler.arun(url=current_url, config=table_config)

            if not result.success:
                print("Failed on table rows:", result.error_message)
                break

            links = json.loads(result.extracted_content)
            # The open() call will now correctly write to the nested directory
            with open(output_filename, "a", encoding="utf-8") as f:
                for item in links:
                    if item["link"]:
                        new_url = urljoin(base_url, item["link"])
                        f.write(new_url + "\n")
                        print(new_url)

            page_count += 1
            print(f" Page {page_count} done.")

            # Extract next page URL
            next_result = await crawler.arun(url=current_url, config=next_button_config)
            if not next_result.success:
                print("Failed on next button:", next_result.error_message)
                break

            next_links = json.loads(next_result.extracted_content)
            if next_links and next_links[0]["next"]:
                next_href = next_links[0]["next"]
                current_url = urljoin(base_url, next_href)
            else:
                print("Reached last page. Exiting.")
                break


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape links from a given URL.")
    parser.add_argument("url", type=str, help="The starting URL to scrape.")
    args = parser.parse_args()
    asyncio.run(extract_all_links(args.url))
