from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from core.exceptions import FileTooLargeError, UnsupportedFileTypeError
from documents.models import Document
from documents.validators import (
    compute_file_sha256,
    compute_url_sha256,
    validate_and_resolve_source_type,
    validate_upload_size,
)


class ResolveSourceTypeTests(SimpleTestCase):
    def test_pdf_extension_resolves_correctly(self):
        self.assertEqual(validate_and_resolve_source_type("report.pdf"), Document.SourceType.PDF)

    def test_docx_extension_resolves_correctly(self):
        self.assertEqual(validate_and_resolve_source_type("notes.docx"), Document.SourceType.DOCX)

    def test_extension_matching_is_case_insensitive(self):
        self.assertEqual(validate_and_resolve_source_type("REPORT.PDF"), Document.SourceType.PDF)

    def test_unsupported_extension_raises(self):
        with self.assertRaises(UnsupportedFileTypeError):
            validate_and_resolve_source_type("malware.exe")

    def test_no_extension_raises(self):
        with self.assertRaises(UnsupportedFileTypeError):
            validate_and_resolve_source_type("noextension")


class UploadSizeTests(SimpleTestCase):
    @override_settings(MAX_UPLOAD_SIZE_MB=1)
    def test_file_within_limit_passes(self):
        f = SimpleUploadedFile("small.txt", b"x" * 100)
        validate_upload_size(f)  # should not raise

    @override_settings(MAX_UPLOAD_SIZE_MB=0.0001)
    def test_file_over_limit_raises(self):
        f = SimpleUploadedFile("big.txt", b"x" * 10000)
        with self.assertRaises(FileTooLargeError):
            validate_upload_size(f)


class Sha256Tests(SimpleTestCase):
    def test_identical_content_produces_identical_hash(self):
        f1 = SimpleUploadedFile("a.txt", b"identical content")
        f2 = SimpleUploadedFile("b.txt", b"identical content")
        self.assertEqual(compute_file_sha256(f1), compute_file_sha256(f2))

    def test_different_content_produces_different_hash(self):
        f1 = SimpleUploadedFile("a.txt", b"content A")
        f2 = SimpleUploadedFile("b.txt", b"content B")
        self.assertNotEqual(compute_file_sha256(f1), compute_file_sha256(f2))

    def test_file_pointer_is_reset_after_hashing(self):
        # Ingestion reads the file again after hashing it (for FileField
        # storage) -- if the pointer isn't rewound, that read comes back empty.
        f = SimpleUploadedFile("a.txt", b"content")
        compute_file_sha256(f)
        self.assertEqual(f.read(), b"content")

    def test_url_hash_normalizes_case_and_surrounding_whitespace(self):
        h1 = compute_url_sha256("https://Example.com/Page")
        h2 = compute_url_sha256("  https://example.com/page  ")
        self.assertEqual(h1, h2)

    def test_different_urls_produce_different_hashes(self):
        h1 = compute_url_sha256("https://example.com/a")
        h2 = compute_url_sha256("https://example.com/b")
        self.assertNotEqual(h1, h2)
