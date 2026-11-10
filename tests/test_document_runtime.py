"""Exercise the installed document workers and existing transport boundaries."""

from hashlib import sha256
import os
from pathlib import Path
import runpy
import subprocess
import sys

import httpx
import pytest

from ai_tender_intelligence.acquisition.http import DocHTTP
from ai_tender_intelligence.acquisition.models import AcquisitionError, Bounds
from ai_tender_intelligence.acquisition.service import Collector
from ai_tender_intelligence.acquisition.store import EvidenceStore
from ai_tender_intelligence.acquisition.validator import validate
from ai_tender_intelligence.semantic.service import Workflow
from test_registration import company, database, setup


CHECKS = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/check_installed_package.py"))
FILE_URL = "https://storage.eop.bg/fixtures/00000000-0000-4000-8000-000000000001"


def test_real_document_workers():
    CHECKS["check_document_workers"]()


@pytest.mark.postgres
def test_acquired_archive_reaches_owned_evidence_retrieval(setup):
    db, profiles, _ = setup
    owner = company(profiles, 101, ready=False)
    other = company(profiles, 202, ready=False)
    evidence = EvidenceStore(db, {101, 202})
    word = CHECKS["word_fixture"]()
    archive = CHECKS["archive"]({"specification.docx": word})
    responses = {
        "GetPublishedTenderDetails": {
            "TenderId": 1,
            "TenderDescriptionDocuments": [{"Id": 2, "Name": "requirements.zip"}],
            "TenderPublicationDetails": [],
        },
        "GetPublishedLots": [],
        "GetPublishedTenderExportsByTenderId": [],
        "GetPublicTenderAnnouncementsByTenderId": [],
        "GetSignedUrlByDocumentId": {
            "Url": FILE_URL,
            "Container": "fixtures",
            "CloudName": FILE_URL.rsplit("/", 1)[-1],
        },
    }

    def respond(request):
        if request.method == "GET":
            assert str(request.url) == FILE_URL
            return httpx.Response(200, content=archive)
        return httpx.Response(200, json=responses[request.url.path.rsplit("/", 1)[-1]])

    http = DocHTTP(transport=httpx.MockTransport(respond))
    try:
        manifest = Collector(evidence, http).run(101, owner.id, 1, (), key="synthetic-document-collection")
    finally:
        http.close()
    assert manifest["status"] == "ACQUIRED", manifest["summary"]
    container, member = sorted(manifest["artifacts"], key=lambda item: item["container_occurrence"] is not None)
    assert container["validation"]["kind"] == "ZIP"
    assert member["container_occurrence"] == container["id"]
    assert member["blob_sha256"] == sha256(word).hexdigest()

    _, index = Workflow(evidence).prepare(101, owner.id, manifest["collection"])
    spans = index.packet("TECHNICAL_SPECIFICATIONS")["spans"]
    assert any(
        span["text"] == CHECKS["SAMPLE_TEXT"]
        and span["source"]["occurrence"] == member["id"]
        and span["source"]["source_sha256"] == member["blob_sha256"]
        for span in spans
    )
    with pytest.raises(AcquisitionError, match="COLLECTION_UNAVAILABLE"):
        evidence.retrieve(202, other.id, manifest["collection"], member["id"])


@pytest.mark.parametrize(("limits", "error"), [
    (Bounds(entries=1), "ARCHIVE_ENTRY_BOUND"),
    (Bounds(ratio=5), "ARCHIVE_EXPANSION_BOUND"),
])
def test_archive_bounds_survive_the_validation_subprocess(limits, error):
    archive = CHECKS["archive"]({"one.json": b" " * 4096 + b"{}", "two.json": b"{}"})
    result = validate(archive, "bounded.zip", limits)
    assert result["valid"] is False and result["error"] == error


def test_archive_depth_boundary_survives_the_validation_subprocess():
    archive = CHECKS["archive"]
    nested = archive({"middle.zip": archive({"too-deep.zip": archive({"data.json": b"{}"})})})
    result = validate(nested, "root.zip", Bounds(depth=1))
    child = result["children"][0]["children"][0]
    assert child["valid"] is False and child["error"] == "ARCHIVE_DEPTH_BOUND"


def test_worker_audit_hook_denies_external_actions():
    env = {name: os.environ[name] for name in ("SystemRoot", "WINDIR") if name in os.environ}
    result = subprocess.run(
        [sys.executable, "-I", "-c", """
import sys
from ai_tender_intelligence.extraction.worker import contain_extraction
contain_extraction()
for event in ('socket.__new__', 'subprocess.Popen', 'os.system'):
    try:
        sys.audit(event)
    except RuntimeError as error:
        assert str(error) == 'EXTRACTION_EXTERNAL_ACTION_DENIED'
    else:
        raise AssertionError(event + ' was allowed')
"""],
        env=env, capture_output=True, text=True, timeout=12,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("length", "data", "error", "reads_body"), [
    ("5", b"12345", "DOWNLOAD_SIZE_BOUND", False),
    ("invalid", b"1234", "INVALID_CONTENT_LENGTH", False),
    ("-1", b"1234", "INVALID_CONTENT_LENGTH", False),
    ("4", b"1234", None, True),
    ("4", b"123", "TRUNCATED_DOWNLOAD", True),
    (None, b"12345", "DOWNLOAD_SIZE_OR_TIME_BOUND", True),
])
def test_document_length_errors_distinguish_header_and_stream(length, data, error, reads_body):
    reads = []

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            reads.append(True)
            yield data

    def response(request):
        headers = {"Content-Length": length} if length is not None else {}
        return httpx.Response(200, headers=headers, stream=Stream())

    http = DocHTTP(Bounds(file_bytes=4), transport=httpx.MockTransport(response))
    try:
        if error:
            with pytest.raises(AcquisitionError, match=f"^{error}$"):
                http.request("GET", FILE_URL)
        else:
            assert http.request("GET", FILE_URL)[0] == data
        assert reads == ([True] if reads_body else [])
        assert http.calls == 1
    finally:
        http.close()
