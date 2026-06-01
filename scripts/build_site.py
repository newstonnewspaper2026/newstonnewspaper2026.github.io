#!/usr/bin/env python3
from __future__ import annotations

import html
import json
import os
import posixpath
import re
import shutil
import sys
import textwrap
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
PUBLIC = ROOT / "public"
PUBLIC_ASSETS = PUBLIC / "assets"
GENERATED_ASSETS = PUBLIC_ASSETS / "generated" / "articles"
CONFIG_PATH = ROOT / "src" / "site_config.json"


@dataclass
class Article:
    slug: str
    section_slug: str
    section_name: str
    title: str
    author: str
    excerpt: str
    body: str
    cover_image: str
    source_file: str
    source_label: str
    date: str
    read_time: str


def main() -> None:
    config = read_json(CONFIG_PATH)
    ensure_input_folders(config)
    articles = collect_articles(config)
    build_dist(config, articles)
    print(f"Built {DIST.relative_to(ROOT)} with {len(articles)} article(s).")


def read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_input_folders(config: dict[str, Any]) -> None:
    for section in config["sections"]:
        folder = ROOT / section["folder"]
        folder.mkdir(parents=True, exist_ok=True)
        keep = folder / ".gitkeep"
        if not keep.exists():
            keep.write_text("", encoding="utf-8")


def collect_articles(config: dict[str, Any]) -> list[Article]:
    article_sources: list[tuple[dict[str, Any], Path]] = []
    for section in config["sections"]:
        folder = ROOT / section["folder"]
        sources = [
            path
            for path in sorted(folder.iterdir())
            if path.is_file()
            and path.suffix.lower() in {".pdf", ".docx"}
            and not path.name.startswith("~$")
        ]
        article_sources.extend((section, path) for path in sources)

    if GENERATED_ASSETS.exists():
        shutil.rmtree(GENERATED_ASSETS)
    GENERATED_ASSETS.mkdir(parents=True, exist_ok=True)

    if not article_sources:
        return []

    fitz = None
    if any(path.suffix.lower() == ".pdf" for _, path in article_sources):
        try:
            import fitz  # type: ignore
        except ModuleNotFoundError as exc:
            raise SystemExit(
                "PDF files were found, but PyMuPDF is not installed. "
                "Run `python3 -m pip install -r requirements.txt`, then rebuild."
            ) from exc

    seen: set[str] = set()
    articles: list[Article] = []
    for section, source_path in article_sources:
        if source_path.suffix.lower() == ".pdf":
            article = parse_pdf(fitz, section, source_path, seen)
        else:
            article = parse_docx(section, source_path, seen)
        articles.append(article)

    articles.sort(key=lambda article: (article.section_name, article.title.lower()))
    return articles


def parse_pdf(fitz: Any, section: dict[str, Any], pdf_path: Path, seen: set[str]) -> Article:
    doc = fitz.open(pdf_path)
    raw_text = "\n\n".join(page.get_text("text") for page in doc)
    lines = normalized_lines(raw_text)

    metadata = doc.metadata or {}
    title = clean_metadata_value(metadata.get("title")) or detect_title(lines, pdf_path)
    author = clean_metadata_value(metadata.get("author")) or detect_author(lines, title)
    body = build_body(raw_text, title, author)
    excerpt = make_excerpt(body)
    if not excerpt:
        excerpt = "Read the full story from the Newston student newsroom."

    base_slug = slugify(title) or slugify(pdf_path.stem)
    slug = unique_slug(f"{section['slug']}-{base_slug}", seen)
    article_assets = GENERATED_ASSETS / slug
    article_assets.mkdir(parents=True, exist_ok=True)

    cover_name = write_cover_image(fitz, doc, article_assets / "cover.png")
    shutil.copy2(pdf_path, article_assets / "source.pdf")

    words = re.findall(r"\b[\w'-]+\b", body)
    read_minutes = max(1, round(len(words) / 220)) if words else 1
    modified = datetime.fromtimestamp(pdf_path.stat().st_mtime)

    doc.close()

    return Article(
        slug=slug,
        section_slug=section["slug"],
        section_name=section["name"],
        title=title,
        author=author or "Newston Staff",
        excerpt=excerpt,
        body=body or "Article text could not be extracted from the PDF. Please open the original PDF below.",
        cover_image=f"assets/generated/articles/{slug}/{cover_name}",
        source_file=f"assets/generated/articles/{slug}/source.pdf",
        source_label="Open original PDF",
        date=modified.strftime("%B %-d, %Y") if os.name != "nt" else modified.strftime("%B %#d, %Y"),
        read_time=f"{read_minutes} min read",
    )


def parse_docx(section: dict[str, Any], docx_path: Path, seen: set[str]) -> Article:
    try:
        with zipfile.ZipFile(docx_path) as docx:
            raw_text = extract_docx_text(docx)
            lines = normalized_lines(raw_text)
            metadata = extract_docx_metadata(docx)

            title = clean_metadata_value(metadata.get("title")) or detect_title(lines, docx_path)
            author = clean_metadata_value(metadata.get("author")) or detect_author(lines, title)
            body = build_body(raw_text, title, author)
            excerpt = make_excerpt(body)
            if not excerpt:
                excerpt = "Read the full story from the Newston student newsroom."

            base_slug = slugify(title) or slugify(docx_path.stem)
            slug = unique_slug(f"{section['slug']}-{base_slug}", seen)
            article_assets = GENERATED_ASSETS / slug
            article_assets.mkdir(parents=True, exist_ok=True)

            cover_name = write_docx_cover(docx, section, title, article_assets)
            shutil.copy2(docx_path, article_assets / "source.docx")
    except zipfile.BadZipFile as exc:
        raise SystemExit(f"{docx_path.name} is not a valid .docx file.") from exc

    words = re.findall(r"\b[\w'-]+\b", body)
    read_minutes = max(1, round(len(words) / 220)) if words else 1
    modified = datetime.fromtimestamp(docx_path.stat().st_mtime)

    return Article(
        slug=slug,
        section_slug=section["slug"],
        section_name=section["name"],
        title=title,
        author=author or "Newston Staff",
        excerpt=excerpt,
        body=body or "Article text could not be extracted from the Word document. Please open the original file below.",
        cover_image=f"assets/generated/articles/{slug}/{cover_name}",
        source_file=f"assets/generated/articles/{slug}/source.docx",
        source_label="Open original Word document",
        date=modified.strftime("%B %-d, %Y") if os.name != "nt" else modified.strftime("%B %#d, %Y"),
        read_time=f"{read_minutes} min read",
    )


def extract_docx_text(docx: zipfile.ZipFile) -> str:
    try:
        xml = docx.read("word/document.xml")
    except KeyError as exc:
        raise SystemExit("The .docx file is missing word/document.xml.") from exc

    root = ElementTree.fromstring(xml)
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", DOCX_NS):
        parts: list[str] = []
        for node in paragraph.iter():
            tag = strip_namespace(node.tag)
            if tag == "t" and node.text:
                parts.append(node.text)
            elif tag == "tab":
                parts.append(" ")
            elif tag in {"br", "cr"}:
                parts.append("\n")
        text = "".join(parts).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def extract_docx_metadata(docx: zipfile.ZipFile) -> dict[str, str]:
    try:
        xml = docx.read("docProps/core.xml")
    except KeyError:
        return {}

    root = ElementTree.fromstring(xml)
    metadata: dict[str, str] = {}
    title = root.find("dc:title", DOCX_NS)
    creator = root.find("dc:creator", DOCX_NS)
    if title is not None and title.text:
        metadata["title"] = title.text
    if creator is not None and creator.text:
        metadata["author"] = creator.text
    return metadata


DOCX_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "dc": "http://purl.org/dc/elements/1.1/",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
}


def strip_namespace(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def clean_metadata_value(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split()).strip()
    noisy = {"untitled", "microsoft word", "pages", "canva"}
    if not value or value.lower() in noisy or looks_like_placeholder_title(value):
        return ""
    if len(value) > 140:
        return ""
    return value


def normalized_lines(text: str) -> list[str]:
    return [" ".join(line.split()) for line in text.splitlines() if " ".join(line.split())]


def detect_title(lines: list[str], pdf_path: Path) -> str:
    for line in lines[:18]:
        if len(line) >= 4 and not is_byline(line) and not looks_like_placeholder_title(line):
            return strip_trailing_punctuation(line)
    return title_from_filename(pdf_path)


def looks_like_placeholder_title(value: str) -> bool:
    compact = " ".join(value.split()).strip().lower()
    return bool(re.match(r"^(tab|page|slide)\s+\d+$", compact))


def title_from_filename(path: Path) -> str:
    title = re.sub(r"^[^\wÁÉÍÓÚÜÑáéíóúüñ¿¡]+", "", path.stem)
    title = re.sub(r"\s+", " ", title.replace("_", " ")).strip()
    return strip_trailing_punctuation(title) or "Newston Article"


def detect_author(lines: list[str], title: str) -> str:
    byline_patterns = [
        r"^(?:by|por)\s*:?\s*(.+)$",
        r"^written\s+by\s*:?\s*(.+)$",
        r"^author\s*:?\s*(.+)$",
        r"^reporter\s*:?\s*(.+)$",
    ]
    for line in lines[:30]:
        for pattern in byline_patterns:
            match = re.match(pattern, line, flags=re.IGNORECASE)
            if match:
                return normalize_author(match.group(1))

    try:
        title_index = next(index for index, line in enumerate(lines[:12]) if similar_text(line, title))
    except StopIteration:
        title_index = -1

    for line in lines[title_index + 1 : title_index + 6]:
        candidate = normalize_author(line)
        if looks_like_name(candidate):
            return candidate

    return "Newston Staff"


def normalize_author(value: str) -> str:
    value = re.sub(r"^(by|por|written by|author|reporter)\s*:?\s+", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" -–—:|")
    parts = [part.strip() for part in re.split(r"\s+-\s+", value) if part.strip()]
    if len(parts) > 1 and any(re.search(r"\b(form|school|sports|global|culture|issue|community)\b", part, re.IGNORECASE) for part in parts[1:]):
        value = parts[0]
    return value[:90]


def looks_like_name(value: str) -> bool:
    if not value or len(value) > 70:
        return False
    if any(char.isdigit() for char in value):
        return False
    words = value.replace("&", " ").split()
    if not 2 <= len(words) <= 5:
        return False
    lower_bad = {"school", "sports", "global", "culture", "issue", "newston", "newton", "college"}
    return not any(word.lower().strip(",.") in lower_bad for word in words)


def build_body(text: str, title: str, author: str) -> str:
    lines = normalized_lines(text)
    output: list[str] = []
    skipped_title = False
    skipped_author = False
    for index, line in enumerate(lines):
        if index < 12 and not skipped_title and similar_text(line, title):
            skipped_title = True
            continue
        if index < 18 and not skipped_author and (is_byline(line) or similar_text(normalize_author(line), author)):
            skipped_author = True
            continue
        output.append(line)
    return "\n\n".join(output).strip()


def is_byline(line: str) -> bool:
    return bool(
        re.match(r"^(by|por|written by|author|reporter)\b", line.strip(), flags=re.IGNORECASE)
    )


def similar_text(a: str, b: str) -> bool:
    a_norm = slugify(a)
    b_norm = slugify(b)
    return bool(a_norm and b_norm and (a_norm == b_norm or a_norm in b_norm or b_norm in a_norm))


def strip_trailing_punctuation(value: str) -> str:
    return value.strip().strip("-–—:|")


def make_excerpt(body: str) -> str:
    compact = re.sub(r"\s+", " ", body).strip()
    if len(compact) <= 210:
        return compact
    shortened = compact[:210].rsplit(" ", 1)[0]
    return f"{shortened}..."


def write_cover_image(fitz: Any, doc: Any, output_path: Path) -> str:
    for page_index in range(len(doc)):
        page = doc[page_index]
        for image_info in page.get_images(full=True):
            xref = image_info[0]
            pix = fitz.Pixmap(doc, xref)
            try:
                if pix.width < 220 or pix.height < 160:
                    continue
                if pix.alpha or pix.n > 4:
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                pix.save(output_path)
                return output_path.name
            finally:
                pix = None

    first_page = doc[0]
    matrix = fitz.Matrix(1.8, 1.8)
    pix = first_page.get_pixmap(matrix=matrix, alpha=False)
    pix.save(output_path)
    return output_path.name


def write_docx_cover(
    docx: zipfile.ZipFile,
    section: dict[str, Any],
    title: str,
    article_assets: Path,
) -> str:
    image_path = first_docx_image_path(docx)
    if image_path:
        extension = normalize_image_extension(image_path)
        if extension:
            cover_name = f"cover{extension}"
            (article_assets / cover_name).write_bytes(docx.read(image_path))
            return cover_name

    cover_name = "cover.svg"
    write_generated_cover_svg(article_assets / cover_name, section["name"], title)
    return cover_name


def first_docx_image_path(docx: zipfile.ZipFile) -> str:
    media_files = [
        name
        for name in docx.namelist()
        if name.startswith("word/media/")
        and normalize_image_extension(name)
        and not name.endswith("/")
    ]
    if not media_files:
        return ""

    relationships = docx_relationships(docx)
    try:
        document_xml = docx.read("word/document.xml")
    except KeyError:
        return sorted(media_files)[0]

    root = ElementTree.fromstring(document_xml)
    for blip in root.findall(".//a:blip", DOCX_NS):
        relationship_id = blip.attrib.get(f"{{{DOCX_NS['r']}}}embed")
        if not relationship_id:
            continue
        target = relationships.get(relationship_id, "")
        candidate = resolve_docx_target(target)
        if candidate in media_files:
            return candidate

    return sorted(media_files)[0]


def docx_relationships(docx: zipfile.ZipFile) -> dict[str, str]:
    try:
        xml = docx.read("word/_rels/document.xml.rels")
    except KeyError:
        return {}

    root = ElementTree.fromstring(xml)
    relationships: dict[str, str] = {}
    for relationship in root:
        relationship_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target", "")
        if relationship_id and relationship.attrib.get("TargetMode") != "External":
            relationships[relationship_id] = target
    return relationships


def resolve_docx_target(target: str) -> str:
    if not target:
        return ""
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join("word", target))


def normalize_image_extension(path: str) -> str:
    extension = Path(path).suffix.lower()
    if extension == ".jpeg":
        return ".jpg"
    if extension in {".png", ".jpg", ".gif", ".webp", ".svg"}:
        return extension
    return ""


def write_generated_cover_svg(output_path: Path, section_name: str, title: str) -> None:
    lines = textwrap.wrap(title, width=22)[:6] or ["Newston"]
    text_lines = "\n".join(
        f'<text x="64" y="{170 + index * 72}" class="title">{html.escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1080" height="1350" viewBox="0 0 1080 1350" role="img" aria-label="{html.escape(title)}">
  <style>
    .paper {{ fill: #fffdf7; }}
    .rule {{ stroke: #2c7a2a; stroke-width: 5; }}
    .strip {{ fill: #2c7a2a; }}
    .tiny {{ fill: rgba(255,253,247,0.76); font-family: Arial, sans-serif; font-size: 26px; font-weight: 800; }}
    .section {{ fill: #2c7a2a; font-family: Arial, sans-serif; font-size: 30px; font-weight: 900; }}
    .title {{ fill: #2c7a2a; font-family: Georgia, 'Times New Roman', serif; font-size: 86px; font-weight: 500; }}
  </style>
  <rect class="paper" width="1080" height="1350"/>
  <line class="rule" x1="64" y1="96" x2="1016" y2="96"/>
  {text_lines}
  <text class="section" x="64" y="1040">{html.escape(section_name.upper())}</text>
  <line class="rule" x1="64" y1="1128" x2="1016" y2="1128"/>
  <rect class="strip" x="0" y="1160" width="1080" height="68"/>
  <text class="tiny" x="52" y="1204">NEWSTON</text>
  <text class="tiny" x="280" y="1204">NEWSTON</text>
  <text class="tiny" x="508" y="1204">NEWSTON</text>
  <text class="tiny" x="736" y="1204">NEWSTON</text>
  <line class="rule" x1="64" y1="1268" x2="1016" y2="1268"/>
</svg>
"""
    output_path.write_text(svg, encoding="utf-8")


def slugify(value: str) -> str:
    value = value.lower()
    replacements = {
        "á": "a",
        "é": "e",
        "í": "i",
        "ó": "o",
        "ú": "u",
        "ñ": "n",
        "ü": "u",
    }
    for original, replacement in replacements.items():
        value = value.replace(original, replacement)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def unique_slug(slug: str, seen: set[str]) -> str:
    candidate = slug
    counter = 2
    while candidate in seen:
        candidate = f"{slug}-{counter}"
        counter += 1
    seen.add(candidate)
    return candidate


def build_dist(config: dict[str, Any], articles: list[Article]) -> None:
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True)
    shutil.copytree(PUBLIC_ASSETS, DIST / "assets", dirs_exist_ok=True)
    (DIST / ".nojekyll").write_text("", encoding="utf-8")
    write_json(DIST / "articles.json", [article.__dict__ for article in articles])

    sections_by_slug = {section["slug"]: section for section in config["sections"]}
    articles_by_section = {
        section["slug"]: [article for article in articles if article.section_slug == section["slug"]]
        for section in config["sections"]
    }

    write_page(DIST / "index.html", render_home(config, articles_by_section), depth=0)
    for section in config["sections"]:
        target = DIST / section["slug"] / "index.html"
        write_page(target, render_section(config, section, articles_by_section[section["slug"]]), depth=1)

    for article in articles:
        section = sections_by_slug[article.section_slug]
        target = DIST / "articles" / article.slug / "index.html"
        write_page(target, render_article(config, section, article), depth=2)


def write_json(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_page(path: Path, body: str, depth: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def render_layout(
    config: dict[str, Any],
    title: str,
    main: str,
    depth: int,
    description: str,
    active: str = "",
) -> str:
    home_url = rel(depth, "index.html")
    css_url = rel(depth, "assets/css/styles.css")
    js_url = rel(depth, "assets/js/site.js")
    favicon_url = rel(depth, "assets/brand/newston-wordmark-logo.png")
    logo_url = rel(depth, "assets/brand/newston-wordmark-logo.png")
    school_logo_url = rel(depth, "assets/brand/newton-college-logo.png")

    nav_items = [
        (section["name"], rel(depth, f"{section['slug']}/"), section["slug"])
        for section in config["sections"]
    ]
    nav_html = "\n".join(
        f'<a class="nav-link{" is-active" if key == active else ""}" href="{href}">{escape(label)}</a>'
        for label, href, key in nav_items
    )
    instagram = ""
    if config.get("instagramUrl"):
        instagram = f'<a class="footer-link" href="{escape(config["instagramUrl"])}" target="_blank" rel="noopener">Instagram</a>'
    join_link = ""
    if config.get("joinUrl"):
        join_link = f'<a class="footer-cta" href="{escape(config["joinUrl"])}" target="_blank" rel="noopener">Join Newston</a>'

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="description" content="{escape(description)}">
    <title>{escape(title)}</title>
    <link rel="icon" href="{favicon_url}" type="image/png">
    <link rel="stylesheet" href="{css_url}">
  </head>
  <body>
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="site-header">
      <div class="header-inner">
        <a class="brand" href="{home_url}" aria-label="Newston home">
          <img class="brand-wordmark" src="{logo_url}" alt="Newston newspaper logo">
        </a>
        <button class="nav-toggle" type="button" aria-expanded="false" aria-controls="site-nav">
          <span class="nav-toggle-line"></span>
          <span class="nav-toggle-line"></span>
          <span class="nav-toggle-line"></span>
          <span class="sr-only">Menu</span>
        </button>
        <nav class="site-nav" id="site-nav" aria-label="Primary navigation">
          <a class="nav-link{" is-active" if active == "home" else ""}" href="{home_url}">About Us</a>
          {nav_html}
        </nav>
      </div>
    </header>
    {render_newston_strip()}
    <main id="main">
      {main}
    </main>
    {render_newston_strip()}
    <footer class="site-footer" id="site-footer">
      <div class="footer-inner">
        <div class="footer-brand">
          <img class="footer-logo" src="{logo_url}" alt="Newston newspaper logo">
        </div>
        <div class="footer-action">
          <p class="footer-kicker">Student journalism</p>
          <h2>Write. Report. Publish.</h2>
          <p>Follow the newsroom or apply to join the next Newston team.</p>
          <div class="footer-links">
            {join_link}
            {instagram}
          </div>
        </div>
        <div class="footer-school">
          <img src="{school_logo_url}" alt="Newton College logo">
        </div>
      </div>
    </footer>
    <script src="{js_url}"></script>
  </body>
</html>
"""


def render_newston_strip() -> str:
    repeated = "".join("<span>NEWSTON</span>" for _ in range(8))
    return f'<div class="newston-strip" aria-hidden="true">{repeated}</div>'


def render_home(config: dict[str, Any], articles_by_section: dict[str, list[Article]]) -> str:
    team_cards = "\n".join(render_team_member(member, 0) for member in config["team"])
    section_cards = "\n".join(
        render_section_feature(section, articles_by_section.get(section["slug"], []), 0)
        for section in config["sections"]
    )
    latest = [article for articles in articles_by_section.values() for article in articles][:6]
    latest_html = render_article_grid(latest, 0, empty_title="No articles yet", empty_copy="New stories will appear here as the newsroom publishes them.")

    main = f"""
      <section class="about-hero">
        <div class="container">
          <div class="hero-rule"></div>
          <div class="masthead-lockup" aria-label="Newston masthead">
            <img class="masthead-logo" src="{rel(0, "assets/brand/newston-wordmark-logo.png")}" alt="Newston newspaper logo">
            <div class="school-lockup">
              <img src="{rel(0, "assets/brand/newton-college-logo.png")}" alt="Newton College logo">
            </div>
          </div>
          <div class="about-grid">
            <div class="about-title-block">
              <h1>About Us</h1>
            </div>
            <div class="about-copy">
              <p>Newston is the official student-led newspaper of Newton College, formed by students from Form II to Form V. A shared passion unites our team for writing, journalism, and the power of storytelling.</p>
              <p>Our mission is to connect our school community to both the world within our walls and the world beyond them. We aim to keep you informed, inspired, and engaged—whether it's through cultural insights, reflections on global issues, the latest updates on sports, or stories from our very own school life.</p>
            </div>
          </div>
          <div class="hero-rule"></div>
        </div>
      </section>

      <section class="container editorial-section" aria-labelledby="editorial-team">
        <div class="section-heading">
          <h2 id="editorial-team">Editorial Team 2026</h2>
        </div>
        <div class="team-grid">
          {team_cards}
        </div>
      </section>

      <section class="soft-band" aria-labelledby="sections-heading">
        <div class="container">
          <div class="section-heading">
            <p class="eyebrow">Sections</p>
            <h2 id="sections-heading">The Newston Desk</h2>
          </div>
          <div class="section-feature-grid">
            {section_cards}
          </div>
        </div>
      </section>

      <section class="container article-section" aria-labelledby="latest-heading">
        <div class="section-heading row-heading">
          <div>
            <p class="eyebrow">Latest</p>
            <h2 id="latest-heading">Recently Published</h2>
          </div>
        </div>
        {latest_html}
      </section>
    """
    return render_layout(
        config,
        "Newston | About Us",
        main,
        depth=0,
        description="Newston is the official student-led newspaper of Newton College.",
        active="home",
    )


def render_section(config: dict[str, Any], section: dict[str, Any], articles: list[Article]) -> str:
    grid = render_article_grid(
        articles,
        1,
        empty_title=f"No {section['name']} articles yet",
        empty_copy="Stories will appear here as this section publishes new work.",
    )
    main = f"""
      <section class="page-intro">
        <div class="container">
          <div class="hero-rule"></div>
          <p class="eyebrow">Newston Section</p>
          <h1>{escape(section["name"])}</h1>
          <p>{escape(section["description"])}</p>
          <div class="hero-rule"></div>
        </div>
      </section>
      <section class="container article-section" aria-labelledby="article-list">
        <div class="section-toolbar">
          <div>
            <p class="eyebrow">Articles</p>
            <h2 id="article-list">All {escape(section["name"])} Stories</h2>
          </div>
          <label class="search-label">
            <span class="sr-only">Filter articles</span>
            <input class="article-search" type="search" placeholder="Search articles" data-search-scope="article-grid">
          </label>
        </div>
        {grid}
      </section>
    """
    return render_layout(
        config,
        f"{section['name']} | Newston",
        main,
        depth=1,
        description=section["description"],
        active=section["slug"],
    )


def render_article(config: dict[str, Any], section: dict[str, Any], article: Article) -> str:
    paragraphs = "\n".join(f"<p>{escape(paragraph)}</p>" for paragraph in article.body.split("\n\n") if paragraph.strip())
    main = f"""
      <article class="article-page">
        <header class="article-hero">
          <div class="container article-hero-grid">
            <div class="article-hero-copy">
              <div class="hero-rule"></div>
              <a class="section-pill" href="{rel(2, f"{section['slug']}/")}">{escape(section["name"])}</a>
              <h1>{escape(article.title)}</h1>
              <p class="article-deck">{escape(article.excerpt)}</p>
              <div class="article-meta">
                <span>By {escape(article.author)}</span>
                <span>{escape(article.read_time)}</span>
                <span>{escape(article.date)}</span>
              </div>
              <div class="hero-rule"></div>
            </div>
            <figure class="article-cover">
              <img src="{rel(2, article.cover_image)}" alt="">
            </figure>
          </div>
        </header>
        <div class="container article-body">
          {paragraphs}
          <p class="source-link"><a href="{rel(2, article.source_file)}">{escape(article.source_label)}</a></p>
        </div>
      </article>
    """
    return render_layout(
        config,
        f"{article.title} | Newston",
        main,
        depth=2,
        description=article.excerpt,
        active=section["slug"],
    )


def render_team_member(member: dict[str, Any], depth: int) -> str:
    image = member.get("image")
    media = (
        f'<img src="{rel(depth, image)}" alt="{escape(member["name"])}">'
        if image
        else f'<div class="team-placeholder" aria-label="{escape(member["name"])} photo pending"></div>'
    )
    return f"""
      <article class="team-card">
        {media}
        {render_newston_strip()}
        <div class="team-card-body">
          <h3>{escape(member["name"])}</h3>
          <p>{escape(member["role"])}</p>
        </div>
      </article>
    """


def render_section_feature(section: dict[str, Any], articles: list[Article], depth: int) -> str:
    count = len(articles)
    label = "article" if count == 1 else "articles"
    return f"""
      <a class="section-feature" href="{rel(depth, f"{section['slug']}/")}">
        <span class="section-feature-title">{escape(section["name"])}</span>
        <span class="arrow-pill" aria-hidden="true">→</span>
        <strong>{count} {label}</strong>
        <p>{escape(section["description"])}</p>
        {render_newston_strip()}
      </a>
    """


def render_article_grid(articles: list[Article], depth: int, empty_title: str, empty_copy: str) -> str:
    if not articles:
        return f"""
          <div class="empty-state">
            <h3>{escape(empty_title)}</h3>
            <p>{escape(empty_copy)}</p>
          </div>
        """
    cards = "\n".join(render_article_card(article, depth) for article in articles)
    return f'<div class="article-grid" data-search-container="article-grid">{cards}</div>'


def render_article_card(article: Article, depth: int) -> str:
    return f"""
      <article class="article-card" data-search-text="{escape((article.title + " " + article.author + " " + article.excerpt).lower())}">
        <a href="{rel(depth, f"articles/{article.slug}/")}" aria-label="Read {escape(article.title)}">
          <div class="article-card-cover">
            <img class="article-card-image" src="{rel(depth, article.cover_image)}" alt="">
          </div>
          {render_newston_strip()}
          <div class="article-card-body">
            <p class="article-card-section">{escape(article.section_name)}</p>
            <h3>{escape(article.title)}</h3>
            <p class="article-card-author">By {escape(article.author)}</p>
            <p>{escape(article.excerpt)}</p>
            <span class="arrow-pill" aria-hidden="true">→</span>
          </div>
        </a>
      </article>
    """


def rel(depth: int, target: str) -> str:
    target = target.lstrip("/")
    if target == "index.html":
        return "./" if depth == 0 else "../" * depth
    prefix = "../" * depth
    return f"{prefix}{target}"


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Build failed: {error}", file=sys.stderr)
        raise
