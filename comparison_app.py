import os
import time
import requests
import difflib
import re
import gradio as gr
from bs4 import BeautifulSoup
from docx import Document
from google import genai
from google.genai.errors import APIError

# --- CONFIGURATION ---
MODEL_NAME = "gemini-2.5-flash"

# --- UTILITY FUNCTIONS ---

def get_page_content_raw(url):
    """
    Fetches the webpage content and returns the raw, visible text content
    after cleaning non-relevant tags (scripts/styles).
    """
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')

        # Aggressively remove noise tags for cleaner input to Gemini
        for tag in soup(['script', 'style', 'head', 'meta', 'link', 'noscript', 'img', 'svg']):
            tag.decompose()
        
        # Extract visible text using a newline separator to help Gemini detect block boundaries
        visible_text = soup.get_text(separator='\n', strip=True)
        return visible_text, None

    except requests.exceptions.RequestException as e:
        return None, f"ERROR: Could not fetch URL. Details: {e}"

def generate_labeled_structure(raw_text_input):
    """
    Uses Gemini to analyze the text and tag content by its semantic role,
    preserving all typos and exact wording.
    """
    if not os.getenv("GEMINI_API_KEY"):
        return None, "FATAL ERROR: GEMINI_API_KEY environment variable not set."
        
    print(f"-> Sending text to Gemini for structured labeling ({MODEL_NAME})...")
    
    # The prompt instructs Gemini to use tags that match the user's expected DOCX labels 
    # (H1, TEASER, MENU ITEM, etc.) but format them like [TAG] Content for clean comparison.
    prompt = f"""
    You are a meticulous content extractor. Your task is to process the following raw webpage text and structure it into a document where every piece of content is labeled by its most likely HTML/UI role.

    You MUST output the original text content exactly as given, including any spelling mistakes or inconsistencies. DO NOT paraphrase or summarize. The only thing you can add are the structural labels.

    Use the following bracketed labels for common elements, based on the input text structure:
    [TITLE_H1]: For the main page title (H1).
    [TITLE_H2]: For major section headings (H2).
    [TEASER_SECTION]: For promotional or summary blocks (teasers).
    [BANNER]: For large, prominent promotional areas.
    [MENU_ITEM]: For navigation links or menu list items.
    [PARAGRAPH]: For standard body text.
    [CAPTION]: For image captions or figure labels.
    [TABLE_CELL]: For content found inside tables.
    [OTHER]: For any text that doesn't fit the above (e.g., footers, utility text).

    Place the label immediately before the content, and ensure each labeled block is on a new line.

    --- RAW WEBPAGE TEXT ---
    {raw_text_input}
    """
    
    try:
        client = genai.Client()
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt
        )
        return response.text.strip(), None
    except APIError as e:
        return None, f"ERROR: Gemini API failed. Check your GEMINI_API_KEY and limits. Details: {e}"
    except Exception as e:
        return None, f"ERROR: An unexpected error occurred: {e}"

def get_docx_content_and_labels(docx_filepath):
    """
    Extracts content from DOCX paragraphs, stripping the user's labels 
    (e.g., 'title(h1) : Hello People' -> '[TITLE_H1] Hello People').
    """
    # Regex to capture the label (group 1) and the content (group 2)
    # Allows for any label followed by a colon and whitespace before the content
    LABEL_PATTERN = re.compile(r"^(.*?)\s*:\s*(.*)$")
    extracted_lines = []
    
    try:
        document = Document(docx_filepath)
        for p in document.paragraphs:
            text = p.text.strip()
            if not text:
                continue

            match = LABEL_PATTERN.match(text)
            
            if match:
                # 1. Normalize the label (e.g., title(h1) -> TITLE_H1)
                raw_label = match.group(1).strip().upper().replace(" ", "_").replace("(", "").replace(")", "")
                content = match.group(2).strip()
                
                # 2. Reformat to match Gemini's output: [TITLE_H1] Hello People
                formatted_label = f"[{raw_label}]"
                extracted_lines.append(f"{formatted_label} {content}")
            else:
                # If no label is found, assume it's a PARAGRAPH and use the default label
                extracted_lines.append(f"[PARAGRAPH] {text}")
                
        return extracted_lines, None
    except Exception as e:
        return None, f"ERROR: Failed to read DOCX file. Details: {e}"

def create_labeled_docx_output(text_content):
    """Saves the Gemini-labeled content into a TXT file for user review."""
    # Saves as .txt for easy review of the labeled output
    output_filepath = f"scraped_labeled_web_content_{int(time.time())}.txt"
    with open(output_filepath, 'w', encoding='utf-8') as f:
        f.write(text_content)
    return output_filepath

# --- CORE COMPARISON LOGIC ---

def compare_texts(expected_lines, actual_text):
    """
    Compares the list of expected (DOCX, labeled) lines against the actual (Gemini-labeled) text.
    The comparison is DOCX-to-Website ONLY (validation).
    This version includes MATCHING lines in the report.
    """
    actual_lines = [line.strip() for line in actual_text.splitlines() if line.strip()]

    # Use SequenceMatcher for detailed, ordered, line-by-line comparison
    differ = difflib.Differ()
    diff = list(differ.compare(expected_lines, actual_lines))

    output_lines = []
    has_mismatches = False
    
    output_lines.append("| Status | Expected Content (from DOCX) | Mismatch Detail |")
    output_lines.append("| :--- | :--- | :--- |")

    # This array will hold all results: matches and mismatches
    comparison_results = []

    for line in diff:
        prefix = line[0]
        content = line[2:].strip()
        
        if not content:
            continue
        
        # --- Handle Matches ---
        if prefix == ' ':
            # Match: Content and structure tag are identical
            comparison_results.append({
                "status": "✅ MATCHING", 
                "expected": content, 
                "detail": "Content and structural tag are identical on the website."
            })
        
        # --- Handle Mismatches/Missing Content ---
        elif prefix == '-':
            # Content is in DOCX but MISSING or MISMATACHED on the website
            has_mismatches = True
            
            # 1. Strip the label to get the core text
            content_only = re.sub(r'^\[.*?\]\s*', '', content).strip()
            
            # 2. Check if the content *without the label* exists anywhere in the actual text
            actual_content_for_search = re.sub(r'\[.*?\]', '', actual_text).strip()
            
            details = "The labeled content was not found."
            if content_only and content_only in actual_content_for_search:
                details = "STRUCTURAL ERROR: Content found on page, but under a DIFFERENT label/structure (e.g., expected [TITLE_H1], found [PARAGRAPH])."
            else:
                details = "CONTENT ERROR: Text is Missing, has Typos, or major Mismatches on the website."
            
            comparison_results.append({
                "status": "❌ MISMATCH", 
                "expected": content, 
                "detail": details
            })
        
        elif prefix == '+':
            # Content is EXTRA on the website, which we ignore for the detailed report but track its existence
            continue 
        
    # --- Build the Final Report Table ---
    for item in comparison_results:
        # Truncate content for table display
        expected_display = item['expected'][:100].replace('\n', ' ') + ('...' if len(item['expected']) > 100 else '')
        output_lines.append(f"| {item['status']} | `{expected_display}` | {item['detail']} |")


    if not has_mismatches:
        report = "## ✅ Perfect Structural Match Found"
        report += "\nAll labeled content in the DOCX was found with the correct structural tag and exact content on the live page."
    else:
        report = "## ⚠️ Structural Mismatches Detected"
        report += "\nThe following is the complete report, including all matches and mismatches."
    
    report += "\n\n" + "\n".join(output_lines)
    report += "\n\n---\n\n## Extra Website Content Flag\n"
    report += "All website content not explicitly listed in the DOCX is considered 'Extra Website Content' (e.g., current date, unique ads) and is not listed in the detailed table above."

    return report

# --- GRADIO INTERFACE FUNCTION ---

def run_structural_validation(url, comparison_file):
    """
    Orchestrates scraping, DOCX label stripping, Gemini structural analysis, and comparison.
    """
    if not comparison_file:
        return None, "ERROR: Please upload a DOCX file for comparison."

    # 1. Scrape the raw visible text
    raw_text, error_scrape = get_page_content_raw(url)
    if error_scrape:
        return None, error_scrape

    # 2. Structural Analysis using Gemini
    labeled_web_content, error_gemini = generate_labeled_structure(raw_text)
    if error_gemini:
        return None, error_gemini

    # 3. Extract and strip labels from DOCX (Expected content)
    docx_labeled_lines, error_docx = get_docx_content_and_labels(comparison_file.name)
    if error_docx:
        return None, error_docx

    # 4. Compare DOCX lines against Labeled Web Content
    validation_report_markdown = compare_texts(docx_labeled_lines, labeled_web_content)
    
    # 5. Save the Labeled Web Content for the user to download
    output_filepath = create_labeled_docx_output(labeled_web_content)

    # 6. Return results to Gradio
    return output_filepath, validation_report_markdown

# --- GRADIO UI DEFINITION ---

url_input = gr.Textbox(
    label="1. Website URL to Validate (Actual Complex Content)",
    placeholder="Enter the full URL (e.g., https://example.com/data)",
    value="https://en.wikipedia.org/wiki/Python_(programming_language)"
)

comparison_input = gr.File(
    label="2. Upload Reference DOCX (Expected Content: e.g., 'title(h1) : Hello People')",
    type="filepath",
    # Removed file_types for compatibility
)

file_output = gr.File(label="A. Download Gemini-Labeled Web Content (For Review)")
validation_report = gr.Markdown(label="B. Structural Validation Report: DOCX vs. Live Website")


iface = gr.Interface(
    fn=run_structural_validation,
    inputs=[url_input, comparison_input],
    outputs=[file_output, validation_report],
    title="Structural Content Validation Tool (AI-Powered)",
    description="This tool uses the Gemini API to analyze complex website structures and tags content. Your DOCX must use the format `label(tag) : content` (e.g., `title(h1) : Welcome`). The tool validates that the content exists with the correct structural tag.",
    # Removed allow_flagging="never" due to Gradio version conflict
)

if __name__ == "__main__":
    iface.launch()