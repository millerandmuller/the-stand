"""Regression test for BUG 1 (2026-08-31 mic-test round 3), the honesty P1.

Reproduced by the user against `the-stand-00017-f2d`: uploading an EMPTY file
to "Upload for Defense" produced a complete, fully-populated "Doctoral
Dissertation Defense Examination" — committee, methodology attack lines, case
file and all. The same empty file at "Upload for Sales" produced an invented
"Enterprise Solution Discovery and Evaluation". Nothing in either case came
from the document, because there was no document; the model simply filled the
gap, and the result was then presented under this product's citation rubric.

That is the one failure this product cannot survive: everything here rests on
cite-or-GAP — every line cites its source where one exists. A case generated
from zero bytes has no source to cite, so it must never be built at all.

Root cause: `witness_agent/case_generator.py` checked only the UPPER size
bound (`MAX_UPLOAD_BYTES`). There was no lower bound, no extraction check and
no grounding check, so empty bytes went straight to the model.

These tests drive the real gate (`assert_document_has_substance`) and the real
HTTP upload path, and assert the two halves of the fix: a refusal for an
upload with no readable content, and NO case generated (the generator is never
even called). A file with real content must still pass through untouched.
"""

import io
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from witness_agent.case_generator import (  # noqa: E402
    MIN_DOCUMENT_CHARS,
    MIN_DOCUMENT_WORDS,
    UnreadableDocumentError,
    assert_document_has_substance,
    extract_document_text,
)

# A real dissertation paragraph's worth of prose — comfortably over both
# thresholds, the shape every legitimate upload has.
REAL_TEXT = (
    "Chapter 4 sets out the methodology used throughout this study. A mixed "
    "methods design was selected because neither the quantitative survey nor "
    "the semi structured interviews alone could account for the observed "
    "variance in adherence rates across the three participating clinics. "
    "Participants were recruited by purposive sampling between March and "
    "September, and the resulting sample of one hundred and eighteen "
    "respondents was stratified by clinic and by length of enrolment. "
    "Inter rater reliability for the coding frame was assessed on a twenty "
    "percent subsample and reached an acceptable threshold. Limitations of "
    "this approach, in particular the single site interview cohort and the "
    "absence of a control arm, are discussed at the end of the chapter."
)


def _image_only_pdf() -> bytes:
    """A structurally valid single-page PDF whose only content is an image —
    no text layer at all, the scanned-document case from the bug report."""
    img = zlib.compress(b"\xff" * (8 * 8 * 3))
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] "
        b"/Resources << /XObject << /Im0 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length 44 >>\nstream\nq 200 0 0 200 0 0 cm /Im0 Do Q\nendstream",
        b"<< /Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace "
        b"/DeviceRGB /BitsPerComponent 8 /Filter /FlateDecode /Length "
        + str(len(img)).encode()
        + b" >>\nstream\n"
        + img
        + b"\nendstream",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n" % (len(objs) + 1))
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objs) + 1, xref)
    )
    return out.getvalue()


# --------------------------------------------------------------------------
# The gate itself
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload,mime,name",
    [
        (b"", "text/plain", "empty.txt"),
        (b"   \n\n\t   \r\n  ", "text/plain", "whitespace.txt"),
        (b"", "application/pdf", "empty.pdf"),
        (b"Too short to ground anything.", "text/plain", "thin.txt"),
    ],
)
def test_documents_without_substance_are_refused(payload, mime, name):
    with pytest.raises(UnreadableDocumentError):
        assert_document_has_substance(payload, mime, name)


def test_image_only_pdf_is_refused():
    """The bug report's third case: a PDF with no text layer. It is a valid,
    non-empty file — only extraction can tell it apart from a real document."""
    pdf = _image_only_pdf()
    assert len(pdf) > 100, "fixture must be a real, non-trivial file"
    assert extract_document_text(pdf, "application/pdf", "scan.pdf").strip() == ""
    with pytest.raises(UnreadableDocumentError):
        assert_document_has_substance(pdf, "application/pdf", "scan.pdf")


def test_real_document_still_passes():
    """The gate must be invisible to legitimate uploads."""
    assert_document_has_substance(REAL_TEXT.encode(), "text/plain", "chapter4.txt")


def test_thresholds_are_the_documented_ones():
    """If someone loosens these, the test says so out loud."""
    assert MIN_DOCUMENT_CHARS == 400
    assert MIN_DOCUMENT_WORDS == 50


def test_unreadable_format_is_not_rejected_on_a_guess():
    """We only ever refuse on positive evidence. A format this module can't
    extract (e.g. .docx) must still go to the model, exactly as before —
    otherwise the fix would start rejecting real documents."""
    assert_document_has_substance(
        b"PK\x03\x04" + b"\x00" * 5000,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "thesis.docx",
    )


# --------------------------------------------------------------------------
# The HTTP path — the actual route the user's empty file travelled
# --------------------------------------------------------------------------


def test_empty_upload_is_refused_over_http_and_generates_no_case(monkeypatch):
    """Drives POST /api/cases/upload with an empty file and asserts both
    halves: a 4xx with a plain-language reason, and the generator never
    called — no case, no grid entry, no template fallback."""
    fastapi_testclient = pytest.importorskip("fastapi.testclient")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-key-not-used")

    from server import app as app_module

    called = {"generate": 0}

    async def _fail_if_called(*args, **kwargs):
        called["generate"] += 1
        raise AssertionError(
            "generate_case_content was reached with an empty document — "
            "this is exactly the bug (a case invented from nothing)"
        )

    monkeypatch.setattr(app_module, "generate_case_content", _fail_if_called)

    client = fastapi_testclient.TestClient(app_module.app)
    before = dict(app_module._uploaded_cases_cache)

    for mode in ("defense", "sales"):
        for filename, payload in (
            ("empty.txt", b""),
            ("whitespace.txt", b"    \n\t  "),
            ("scan.pdf", _image_only_pdf()),
        ):
            res = client.post(
                "/api/cases/upload",
                data={"mode": mode, "owner_token": "test-owner-token"},
                files={"file": (filename, payload, "application/octet-stream")},
            )
            assert res.status_code == 422, (
                f"{mode}/{filename} returned {res.status_code}, expected a refusal"
            )
            detail = res.json().get("detail", "")
            assert "no case can be built" in detail.lower(), detail

    assert called["generate"] == 0
    assert app_module._uploaded_cases_cache == before, (
        "a refused upload must never land in the case cache"
    )
