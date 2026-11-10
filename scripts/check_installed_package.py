"""Exercise the installed runtime with synthetic inputs and no external services."""
from io import BytesIO
from pathlib import Path
import sys
from zipfile import ZIP_DEFLATED, ZipFile


SAMPLE_TEXT = "Примерно изискване за изпълнение"


def archive(files):
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    return output.getvalue()


def word_fixture():
    return archive({
        "[Content_Types].xml": (
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '</Types>'
        ),
        "_rels/.rels": (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="word/document.xml" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>'
            '</Relationships>'
        ),
        "word/document.xml": (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f'<w:body><w:p><w:r><w:t>{SAMPLE_TEXT}</w:t></w:r></w:p></w:body></w:document>'
        ),
    })


def check_document_workers():
    from openpyxl import Workbook
    from pypdf import PdfWriter
    from ai_tender_intelligence.acquisition.validator import validate
    from ai_tender_intelligence.extraction.models import fingerprint
    from ai_tender_intelligence.extraction.runner import run

    pdf = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=144, height=144)
    writer.write(pdf)
    workbook = Workbook()
    workbook.active.append(["Synthetic specification", 12])
    xlsx = BytesIO()
    workbook.save(xlsx)
    word = word_fixture()
    zipped = archive({"tender/requirement.docx": word, "metadata.json": b'{"fixture": true}'})
    for name, data, expected in (
        ("sample.pdf", pdf.getvalue(), "PDF"),
        ("sample.docx", word, "DOCX"),
        ("sample.zip", zipped, "ZIP"),
        ("sample.xlsx", xlsx.getvalue(), "XLSX"),
        ("sample.xml", b"<document><quantity>12</quantity></document>", "XML"),
        ("sample.json", b'{"fixture": true}', "JSON"),
    ):
        result = validate(data, name)
        if not result["valid"] or result["kind"] != expected:
            raise ValueError(f"Document validator subprocess failed for {name}: {result.get('error', result['kind'])}")
        if expected == "ZIP" and [
            (child["name"], child["kind"], child["valid"]) for child in result["children"]
        ] != [("tender/requirement.docx", "DOCX", True), ("metadata.json", "JSON", True)]:
            raise ValueError("Document validator subprocess lost ZIP member identities or formats")

    for name, data, expected in (
        ("broken.pdf", b"%PDF-1.7\ninvalid", "INVALID_PDF"),
        ("broken.docx", archive({"[Content_Types].xml": b"<Types/>"}), "INVALID_OOXML"),
        ("broken.zip", b"PK\x03\x04broken", "CORRUPT_ARCHIVE"),
        ("broken.doc", bytes.fromhex("d0cf11e0a1b11ae1"), "CORRUPT_OLE"),
        ("unsupported.exe", b"MZunsupported", "UNSUPPORTED_OR_MISLEADING_FILE"),
        ("traversal.zip", archive({"../outside.json": b"{}"}), "UNSAFE_ARCHIVE_PATH"),
    ):
        result = validate(data, name)
        if result["valid"] or result.get("error") != expected:
            raise ValueError(f"Expected {expected} from the document validator subprocess for {name}")

    fingerprint("DOCX")
    extracted = run(word, "DOCX")
    if extracted["status"] != "EXTRACTED" or not any(
        block.get("text") == SAMPLE_TEXT and block.get("locator") for block in extracted["blocks"]
    ):
        raise ValueError(f"Document extraction subprocess failed for sample.docx: {extracted['flags']}")
    print("Document workers verified: supported formats, malformed input, archive paths and DOCX extraction")


def check():
    from ai_tender_intelligence.analysis.control45 import recipe
    from ai_tender_intelligence.api import create_app
    from ai_tender_intelligence.assets import root
    from ai_tender_intelligence.beta67.__main__ import main
    from ai_tender_intelligence.bootstrap import setup
    from ai_tender_intelligence.catalogue.source import load_pinned
    from ai_tender_intelligence.classification.contract import RESPONSE_SCHEMA, VERSION
    from ai_tender_intelligence.classification.prompts import load_prompts
    from ai_tender_intelligence.semantic.processing58 import instructions

    assets = root()
    expected = Path(sys.prefix) / 'share/ai-tender-intelligence'
    if assets.resolve() != expected.resolve():
        raise ValueError('Expected installed wheel resources, not source checkout')
    load_pinned()
    load_prompts(RESPONSE_SCHEMA, VERSION)
    instructions()
    recipe()
    for folder in ('migrations', 'discovery_migrations', 'document_migrations', 'extraction_migrations'):
        if not (assets/folder/'env.py').is_file() or not list((assets/folder/'versions').glob('*.py')):
            raise ValueError(f'Missing installed migration resources in {folder}')
    if not all(callable(entry) for entry in (main, setup, create_app)):
        raise ValueError('Missing installed Telegram, database setup or API entrypoint')
    print('Installed entrypoints, migrations, prompts and verified CPV data are available')
    check_document_workers()


if __name__ == '__main__':
    check()
