#!/bin/bash

# Exit immediately if a command exits with a non-zero status.
# This prevents the script from continuing if a step fails.
set -e

# Check if a URL argument is provided
if [ -z "$1" ]; then
  echo "Error: No starting URL provided."
  echo "Usage: docker run <image_name> \"http://your-start-url.com\""
  exit 1
fi

# Assign the first argument to a variable for clarity
START_URL="$1"

echo "=========================================================="
echo "  PIPELINE STARTED"
echo "  Start URL: ${START_URL}"
echo "=========================================================="

# --- Step 1: Extract page links ---
echo -e "\n--> STEP 1: Running pdf_page_link_extractor_v2.py..."
python3 pdf_page_link_extractor_v2.py "${START_URL}"

# --- Step 2: Extract PDF links ---
echo -e "\n--> STEP 2: Running pdf_link_extractor.py..."
python3 pdf_link_extractor.py "${START_URL}"

# --- Step 3: Run the final scraper ---
echo -e "\n--> STEP 3: Running finalScraperv2.py..."
python3 finalScraperv2.py "${START_URL}"

echo -e "\n=========================================================="
echo "  PIPELINE FINISHED SUCCESSFULLY!"
echo "=========================================================="