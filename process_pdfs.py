from pathlib import Path
import fitz  # PyMuPDF
import json
import re
from collections import Counter

def cluster_font_sizes(sizes, threshold=0.5):
    """
    Cluster font sizes to group similar ones.
    Returns list of cluster centers sorted descending.
    """
    if not sizes:
        return []
    sizes = sorted(sizes, reverse=True)
    clusters = []
    cluster = [sizes[0]]

    for size in sizes[1:]:
        if abs(size - cluster[-1]) / cluster[-1] < threshold:
            cluster.append(size)
        else:
            clusters.append(sum(cluster)/len(cluster))
            cluster = [size]
    clusters.append(sum(cluster)/len(cluster))
    return sorted(clusters, reverse=True)

def font_size_cluster(font_size, clusters):
    """
    Returns the cluster index font_size belongs to.
    Cluster 0 = largest size
    """
    for i, center in enumerate(clusters):
        if abs(font_size - center) / center < 0.5:  # within 50%
            return i
    return len(clusters)  # unknown

def is_numbered_heading(text):
    return bool(re.match(r'^(\d+(\.\d+)+)(\.|\s+)', text.strip()))

def heading_level_from_numbering(text):
    """
    Count dots to assign level, max H4.
    """
    m = re.match(r'^(\d+(\.\d+)+)(\.|\s+)', text.strip())
    if not m:
        return None
    dot_count = m.group(1).count('.')
    if dot_count == 0:
        return "H1"
    elif dot_count == 1:
        return "H2"
    elif dot_count == 2:
        return "H3"
    else:
        return "H4"

def is_valid_heading(text, font_size, median_font, alpha_threshold=0.4):
    clean = text.strip()
    if len(clean) < 5 or len(clean) > 100:
        return False
    alpha_chars = sum(c.isalpha() for c in clean)
    if alpha_chars / max(1,len(clean)) < alpha_threshold:
        # but allow valid numbered headings with text after numbering
        if not re.match(r'^(\d+(\.\d+)*)(\.|\s+)(.+)', clean):
            return False
    if re.search(r'[+=_/\\@#$%^&*]', clean):
        if not re.match(r'^(\d+(\.\d+)*)(\.|\s+)(.+)', clean):
            return False
    if font_size < median_font - 1:
        # likely body text
        return False
    return True

def assign_heading_level(text, font_size, median_font, font_cluster_idx, is_bold):
    # Priority 1: Numbered headings
    if is_numbered_heading(text):
        lvl = heading_level_from_numbering(text)
        if lvl:
            return lvl

    # Priority 2: Font size cluster and boldness heuristics
    # Map cluster to level (cluster 0 is largest size → H1)
    # Bold text reduces level by 1 where applicable
    # Clamp levels to H1-H4
    level_map = {0: "H1", 1: "H2", 2: "H3", 3: "H4"}

    base_level = level_map.get(font_cluster_idx, "H4")
    if is_bold and base_level != "H1":
        # promote one level up by reducing the number (e.g. H3 → H2)
        levels = ["H1", "H2", "H3", "H4"]
        idx = levels.index(base_level)
        base_level = levels[max(0, idx - 1)]
    return base_level

def extract_title(page):
    blocks = page.get_text("dict")["blocks"]
    max_size = 0
    candidate_title = ""
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span["text"].strip()
                sz = span["size"]
                if sz > max_size and txt and len(txt) > 7 and not txt.isupper():
                    max_size = sz
                    candidate_title = txt
    if candidate_title:
        return candidate_title
    # fallback longest line
    longest = ""
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span["text"].strip()
                if len(txt) > len(longest) and len(txt) > 7:
                    longest = txt
    if longest:
        return longest
    # fallback first significant text
    for block in blocks:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                txt = span["text"].strip()
                if len(txt) > 7:
                    return txt
    return ""

def process_single_pdf(pdf_path, output_dir):
    doc = fitz.open(pdf_path)
    all_font_sizes = []
    for page in doc:
        blocks = page.get_text("dict")["blocks"]
        sizes_on_page = [span["size"] for block in blocks for line in block.get("lines", []) for span in line.get("spans", [])]
        all_font_sizes.extend(sizes_on_page)
    font_clusters = cluster_font_sizes(all_font_sizes)
    outline = []
    seen_texts = set()

    for page_idx, page in enumerate(doc, start=1):
        blocks = page.get_text("dict")["blocks"]
        font_sizes_page = [span["size"] for block in blocks for line in block.get("lines", []) for span in line.get("spans", [])]
        if font_sizes_page:
            median_font = sorted(font_sizes_page)[len(font_sizes_page)//2]
        else:
            median_font = 0

        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span["text"].strip()
                    if not text or text in seen_texts:
                        continue
                    seen_texts.add(text)

                    if not is_valid_heading(text, span["size"], median_font):
                        continue

                    font_cluster_idx = font_size_cluster(span["size"], font_clusters)

                    is_bold = (span.get("flags", 0) & 2) > 0

                    level = assign_heading_level(text, span["size"], median_font, font_cluster_idx, is_bold)

                    outline.append({
                        "level": level,
                        "text": text,
                        "page": page_idx
                    })

    title = extract_title(doc[0]).strip()
    output = {
        "title": title,
        "outline": outline
    }
    out_file = output_dir / (Path(pdf_path).stem + ".json")
    with open(out_file, "w", encoding="utf-8") as fout:
        json.dump(output, fout, ensure_ascii=False, indent=2)

def main():
    input_dir = Path("/app/input")
    output_dir = Path("/app/output")
    output_dir.mkdir(exist_ok=True)
    for pdf_file in sorted(input_dir.glob("*.pdf")):
        process_single_pdf(pdf_file, output_dir)

if __name__ == "__main__":
    main()
