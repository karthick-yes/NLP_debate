import os
import asyncio
import urllib.request

# from crawl4ai import AsyncWebCrawler, CrawlerRunConfig # Not used in this version of the script
import requests
from html.parser import HTMLParser
import urllib.parse as up
import argparse  # Import argparse for command-line arguments
import re  # Import re for sanitizing filenames


# Learnt how to use the classic parser, how to deal with the starttag, endtag and so on
class Myparser(HTMLParser):
    def __init__(self, *, convert_charrefs=True):
        super().__init__(convert_charrefs=convert_charrefs)
        self.hyperlinks = []
        self.isinTable = False
        self.next_page_link = None  # To store the next page link if found

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        # Check for table data with headers="t4" for PDF links
        if tag == "td" and attrs.get("headers") == "t4":
            self.isinTable = True
            # print("Entering <td headers = 't4' >") # Debugging print

        # Extract 'a' tags within the table data for PDF links
        if tag == "a" and self.isinTable:
            if "href" in attrs:
                # print("found <a> with href:", attrs['href']) # Debugging print
                self.hyperlinks.append(attrs["href"])

        # Check for the "next page" button (a.pull-right)
        if tag == "a" and "class" in attrs and "pull-right" in attrs["class"]:
            if "href" in attrs:
                self.next_page_link = attrs["href"]
                # print(f"Found next page link: {self.next_page_link}") # Debugging print

    def handle_endtag(self, tag):
        # Exit table data context
        if tag == "td" and self.isinTable:
            self.isinTable = False
            # print("Exiting <td headers = t4 > ") # Debugging print


def get_hyperlinks(url):
    # Understood what headers, exactly are and what kind of measures
    # websites have in place to fight the crawlers.
    header = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://eparlib.nic.in",
    }
    try:
        # Disable SSL verification due to 'verify=False' in original code
        # In a production environment, you should address SSL certificate issues
        # instead of disabling verification.
        response = requests.get(url, headers=header, verify=False, timeout=10)
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)
        if not response.headers.get("content-type", "").startswith("text/html"):
            print(
                f"Skipping non-HTML content type: {response.headers.get('content-type')} for {url}"
            )
            return [], None
        html = response.text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching {url}: {e}")
        return [], None
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
        return [], None

    parser = Myparser()
    parser.feed(html)

    return parser.hyperlinks, parser.next_page_link


async def main_extractor(start_url: str):
    base_url = "https://eparlib.nic.in/"
    current_url = start_url

    # Sanitize the URL to create a valid filename
    # Remove protocol (http/https) and slashes, replace other non-alphanumeric with underscore
    filename_base = re.sub(r"https?://", "", start_url)
    filename_base = re.sub(r"[^a-zA-Z0-9_.-]", "_", filename_base)
    # Ensure filename doesn't start or end with underscore if possible
    filename_base = filename_base.strip("_")
    # Limit filename length and add .txt extension
    output_filename = f"{filename_base[:100]}.txt"

    # Ensure the directory for output files exists
    output_dir = os.path.join(
        "link_extractor_scripts", "extracted_links"
    )  # Change this line
    os.makedirs(output_dir, exist_ok=True)
    full_output_path = os.path.join(output_dir, output_filename)

    print(f"Saving extracted links to: {full_output_path}")

    page_count = 0
    # Clear the file content if it exists from a previous run for the same URL
    if os.path.exists(full_output_path):
        with open(full_output_path, "w", encoding="utf-8") as f:
            f.write("")  # Clear content

    while current_url:
        page_count += 1
        print(f"Crawling page {page_count}: {current_url}")

        links_on_page, next_page_relative_link = get_hyperlinks(current_url)

        if not links_on_page and not next_page_relative_link and page_count > 1:
            print(
                f"No links found and no next page link on {current_url}. Assuming end of pagination."
            )
            break

        with open(full_output_path, "a", encoding="utf-8") as f:
            for link in links_on_page:
                if link:
                    absolute_link = up.urljoin(base_url, link)
                    f.write(absolute_link + "\n")
                    # print(f"  Extracted: {absolute_link}") # Print for real-time progress

        if next_page_relative_link:
            current_url = up.urljoin(base_url, next_page_relative_link)
        else:
            print("Reached the last page or no next page button found. Exiting.")
            current_url = None  # End the loop


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Extract PDF page links from a given URL and save them to a file."
    )
    parser.add_argument("url", type=str, help="The starting URL to scrape.")
    args = parser.parse_args()

    # Run the asynchronous main_extractor function
    asyncio.run(main_extractor(args.url))
