import os
import json
import re
import statistics
from pathlib import Path
import fitz  # pymupdf


class PDFOutlineExtractor:
    def __init__(self):
        pass

    def clean_text(self, text):
        return re.sub(r'\s+', ' ', text.strip())

    def cluster_font_sizes(self, font_sizes):
        font_sizes = sorted(set(font_sizes), reverse=True)
        return font_sizes[:4]

    def font_size_to_level(self, size, clusters):
        if not clusters:
            return "H1"
        for idx, thresh in enumerate(clusters):
            if abs(size - thresh) < 0.01:
                return f"H{idx + 1}"
        return f"H{min(len(clusters) + 1, 4)}"

    def heading_level_from_numbering(self, text):
        m = re.match(r'^(\d+)(\.(\d+))*', text.strip())
        if not m:
            return None
        dots = text.count('.')
        if dots == 0:
            return "H1"
        if dots == 1:
            return "H2"
        if dots == 2:
            return "H3"
        return "H4"

    def is_bold(self, flags):
        # PyMuPDF bold flag check (bit 2 is bold)
        return bool(flags & 2)

    def is_heading_candidate(self, text, size, clusters, flags, rel_x, is_form_like):
        text = self.clean_text(text)
        if not text or len(text) < 6 or len(text) > 90:
            return False

        if re.fullmatch(r'(\(?\d{1,2}\)?[\.:]?)', text):
            return False

        # Must have at least two alphabetic words for forms
        words = [w for w in text.split() if any(c.isalpha() for c in w)]
        if is_form_like and len(words) < 2:
            return False

        only_alpha = re.sub(r'[^a-zA-Z]', '', text)
        only_symbol = re.sub(r'[a-zA-Z0-9\s]', '', text)
        if len(only_symbol) > len(only_alpha):
            return False

        alpha_ratio = sum(c.isalpha() for c in text) / len(text)
        if alpha_ratio < 0.45:
            return False

        numbered = re.match(r'^\d+(\.\d+)+\s+([A-Za-z].+)', text)
        if numbered and len(numbered.group(2).strip()) > 4:
            return True

        roman = re.match(r'^([IVXLCDM]+)[\. \-]+([A-Za-z].+)', text, re.I)
        if roman and len(roman.group(2).strip()) > 4:
            return True

        keywords = [
            'table of contents', 'revision history', 'acknowledgements', 'summary',
            'conclusion', 'abstract', 'introduction', 'appendix', 'objectives', 'overview',
            'references', 'section', 'phase', 'business plan', 'award', 'proposal', 'requirements',
            'milestones', 'terms of reference', 'membership', 'criteria', 'process', 'responsibilities',
            'timeline', 'contract', 'approach', 'funding', 'financial', 'policy', 'administrative',
            'learning objectives', 'career paths'
        ]
        lower = text.lower()
        if any(lower.startswith(kw) for kw in keywords):
            return True

        is_bold = self.is_bold(flags)
        if size in clusters or (is_bold and size > max(clusters + [0])):
            return True

        uppercase_ratio = sum(1 for c in text if c.isupper()) / len(text)
        if not only_symbol and uppercase_ratio > 0.6 and len(words) >= 2 and len(text) < 70:
            return True

        if text.endswith(":") and len(text) < 55:
            return True

        return False

    def extract_title(self, page_data):
        best = ""
        max_size = 0
        for elem in page_data:
            txt = self.clean_text(elem['text'])
            if not txt or len(txt) < 6 or txt.isupper():
                continue
            if txt.endswith('.') or txt.endswith(":") or len(txt) > 120:
                continue
            if elem['size'] > max_size and elem['relative_y'] < 0.3:
                max_size = elem['size']
                best = txt
        if best:
            return best
        for elem in page_data:
            txt = self.clean_text(elem['text'])
            if len(txt) > len(best) and elem['relative_y'] < 0.4:
                best = txt
        return best

    def detect_form_like(self, page_data):
        symbols = 0
        short_caps = 0
        for elem in page_data:
            txt = self.clean_text(elem['text'])
            if len(txt) < 14 and sum(1 for c in txt if not c.isalnum()) / max(1, len(txt)) > 0.2:
                symbols += 1
            if txt.isupper() and len(txt) < 13:
                short_caps += 1
        return (symbols + short_caps) > (len(page_data) * 0.18)

    def process_pdf(self, pdf_path, output_dir):
        doc = fitz.open(pdf_path)
        all_elements = []

        for page in doc:
            page_rect = page.rect
            width = page_rect.width
            height = page_rect.height
            blocks = page.get_text("dict")["blocks"]
            page_items = []
            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            text = span.get("text", "").strip()
                            if not text:
                                continue
                            bbox = span["bbox"]
                            page_items.append({
                                "text": text,
                                "size": span["size"],
                                "flags": span["flags"],
                                "page": page.number + 1,
                                "x": bbox[0], "y": bbox[1], "width": bbox[2] - bbox[0], "height": bbox[3] - bbox[1],
                                "relative_x": bbox[0] / width if width else 0.0,
                                "relative_y": bbox[1] / height if height else 0.0,
                            })
            all_elements.append(page_items)

        font_sizes = [elem["size"] for page in all_elements for elem in page]
        clusters = self.cluster_font_sizes(font_sizes)
        is_form_like = any(self.detect_form_like(page) for page in all_elements[:2])

        title = self.extract_title(all_elements[0]) if all_elements else ""

        dedup = set()
        outline = []
        for page_i, page_data in enumerate(all_elements):
            for elem in page_data:
                t = self.clean_text(elem['text'])
                if not t or (t == title):
                    continue
                if not self.is_heading_candidate(t, elem['size'], clusters, elem['flags'], elem['relative_x'], is_form_like):
                    continue
                key = (t, elem['page'])
                if key in dedup:
                    continue
                dedup.add(key)

                numbered_lvl = self.heading_level_from_numbering(t)
                if numbered_lvl:
                    lvl = numbered_lvl
                else:
                    lvl = self.font_size_to_level(elem["size"], clusters)
                outline.append({
                    "level": lvl,
                    "text": t,
                    "page": elem['page']
                })

        result = {
            "title": title,
            "outline": outline
        }
        out_file = output_dir / (Path(pdf_path).stem + ".json")
        with open(out_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    def process_directory(self, input_dir="/app/input", output_dir="/app/output"):
        input_path = Path(input_dir)
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        for pdf_file in input_path.glob("*.pdf"):
            self.process_pdf(pdf_file, output_path)


def main():
    extractor = PDFOutlineExtractor()
    extractor.process_directory("/app/input", "/app/output")


if __name__ == "__main__":
    main()
