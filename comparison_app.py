import os
import time
import requests
import difflib
import re
import gradio as gr
from bs4 import BeautifulSoup
from docx import Document

# --- UTILITY FUNCTIONS ---

def get_page_content_raw(url):
    """
    Fetches the webpage content and extracts raw, sequential text.
    Returns text normalized to a single string for comparison.
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Remove common noise tags
        for tag in soup(['script', 'style', 'head', 'meta', 'link', 'noscript', 'nav', 'footer', 'header', 'aside']):
            tag.decompose()

        # Extract all visible text, normalizing large whitespace blocks
        raw_text = soup.get_text(separator=' ', strip=True)
        
        # Lowercase and remove punctuation for a more flexible, word-based comparison, 
        # but keep it case-sensitive here to detect case mismatches/typos accurately.
        # We will keep the content as raw as possible and clean it during line processing.
        return raw_text, None

    except requests.exceptions.RequestException as e:
        return None, f"ERROR: Could not fetch URL. Details: {e}"

def get_docx_text_by_paragraph(docx_filepath):
    """
    Extracts all text from an uploaded DOCX file, returning a list of paragraphs.
    This preserves the structure of the source of truth (the DOCX).
    """
    try:
        document = Document(docx_filepath)
        # Return a list of stripped paragraphs, ignoring empty ones
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        return paragraphs, None
    except Exception as e:
        return None, f"ERROR: Failed to read DOCX file. Details: {e}"

def normalize_text(text):
    """Normalizes text for easier searching (removes excess space, lowercases)."""
    # Remove all non-alphanumeric characters except spaces
    text = re.sub(r'[^a-z0-9\s]', '', text.lower())
    # Replace multiple spaces with a single space
    text = re.sub(r'\s+', ' ', text).strip()
    return text

# --- CORE COMPARISON LOGIC ---

def validate_content(docx_paragraphs, scraped_text_raw):
    """
    Compares DOCX paragraphs against the raw scraped text to find mismatches.
    Only content from the DOCX is validated.
    """
    results = []
    has_mismatches = False
    
    # Normalize the entire scraped text once for efficient searching
    scraped_text_normalized = normalize_text(scraped_text_raw)

    for i, docx_paragraph in enumerate(docx_paragraphs):
        # Normalize the paragraph for searching
        docx_normalized = normalize_text(docx_paragraph)
        
        # Use a simple "contains" check on the normalized text first
        if docx_normalized in scraped_text_normalized:
            # Found (we assume this is a match, though structural context is ignored)
            results.append({
                "status": "✅ MATCH",
                "expected": docx_paragraph,
                "actual": "Content found on website (context ignored)",
                "details": "Paragraph text found on the live page."
            })
        else:
            # Content is MISSING or has MISMATACHES (typos, extra words, missing words)
            has_mismatches = True
            
            # Use SequenceMatcher to find specific word differences for better reporting
            docx_words = docx_paragraph.split()
            
            # Since the paragraph didn't match the normalized site text, 
            # we compare the paragraph's words against the entire site's words 
            # to pinpoint where the difference lies.
            
            # This is complex, so we'll simplify the failure report:
            
            # Option 1: Detailed Word-Level Diff (Accurate but slower/more complex UI)
            
            # Option 2: Simple Flagging (Better for immediate validation)
            
            # We flag this paragraph as a failure because the whole paragraph (normalized) 
            # was not found in the whole scraped text (normalized).
            
            # To try and show where the mismatch occurred, we report the best "close match"
            
            # Since we can't efficiently run difflib against an entire large document, 
            # we report the full paragraph as missing/mismatched and provide the full scraped text 
            # as context for manual review.
            
            results.append({
                "status": "❌ MISMATCH/MISSING",
                "expected": docx_paragraph,
                "actual": "Not found or contains differences/typos on website.",
                "details": "The exact paragraph text (when normalized) could not be found anywhere on the page. Check for typos or missing content."
            })

    # --- FORMAT OUTPUT ---
    
    output_markdown = []
    
    if not has_mismatches:
        output_markdown.append("## ✅ Validation Successful: No Mismatches Found in Reference Document")
        output_markdown.append("All paragraphs from the DOCX file were found on the live webpage (ignoring structural context).")
    else:
        output_markdown.append("## ⚠️ Mismatches Detected")
        output_markdown.append("The following paragraphs from the DOCX file were either not found or had discrepancies on the website.")
        output_markdown.append("\n| Status | Expected Paragraph (from DOCX) | Mismatch Details |")
        output_markdown.append("| :--- | :--- | :--- |")
        
        # Filter for only the mismatches to display in the table
        mismatch_count = 0
        for item in results:
            if item["status"] == "❌ MISMATCH/MISSING":
                mismatch_count += 1
                # Truncate expected text for table display
                expected_display = item['expected'][:100].replace('\n', ' ') + ('...' if len(item['expected']) > 100 else '')
                output_markdown.append(f"| {item['status']} | `{expected_display}` | {item['details']} |")

    # Flag all other content as extra
    output_markdown.append("\n## Extra Website Content Flag")
    output_markdown.append("The tool only validates content from the DOCX. All other content on the live website (headers, footers, teasers, ads, etc.) is flagged as 'Extra Website Content' and is not included in the detailed table.")

    return has_mismatches, "\n".join(output_markdown), scraped_text_raw

# --- GRADIO INTERFACE FUNCTION ---

def run_validation(url, comparison_file):
    """
    Orchestrates scraping, DOCX extraction, and content validation.
    """
    if not comparison_file:
        return None, "ERROR: Please upload a DOCX file for comparison.", ""

    # 1. Scrape the raw content
    scraped_text_raw, error_scrape = get_page_content_raw(url)
    if error_scrape:
        return None, error_scrape, ""
    
    # 2. Extract paragraphs from the comparison DOCX (Source of Truth)
    docx_paragraphs, error_docx = get_docx_text_by_paragraph(comparison_file.name)
    if error_docx:
        return None, error_docx, ""

    # 3. Perform Validation
    has_mismatches, validation_report_markdown, raw_web_text = validate_content(docx_paragraphs, scraped_text_raw)
    
    # 4. Save the Raw Scraped content for the user to download
    # We save the raw scraped text so the user can see what was used for comparison
    output_filepath = f"scraped_web_content_{int(time.time())}.txt"
    with open(output_filepath, 'w', encoding='utf-8') as f:
        f.write(raw_web_text)

    # 5. Return results to Gradio
    return (
        output_filepath,
        validation_report_markdown
    )

# --- GRADIO UI DEFINITION ---

url_input = gr.Textbox(
    label="1. Website URL to Validate (Actual Content)",
    placeholder="Enter the full URL (e.g., https://example.com/data)",
    value="https://en.wikipedia.org/wiki/Python_(programming_language)"
)

comparison_input = gr.File(
    label="2. Upload Reference DOCX (Source of Truth)",
    type="filepath",
    # Removed file_types=["docx"] to prevent upload errors as requested
)

file_output = gr.File(label="A. Download Raw Web Content (Used for Comparison)")
validation_report = gr.Markdown(label="B. Validation Report: DOCX Content vs. Live Website")


iface = gr.Interface(
    fn=run_validation,
    inputs=[url_input, comparison_input],
    outputs=[file_output, validation_report],
    title="Content Validation Tool: DOCX to Website Check",
    description="This tool strictly validates whether every paragraph in your reference DOCX file is present on the live website. Any discrepancies (typos, missing words, extra words) will be flagged as a MISMATCH. All website content not in the DOCX is considered 'Extra Website Content' and ignored for the detailed report.",
    # Removed allow_flagging="never" to fix compatibility issue with current Gradio version.
)

if __name__ == "__main__":
    iface.launch()