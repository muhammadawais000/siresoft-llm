"""Each user only sees, manages and searches the documents they uploaded."""

import uuid

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from unittest.mock import patch

from documents.models import Document, allowed_document_ids
from rag.retrieval import get_all_chunks


def make_user(email):
    return User.objects.create_user(username=email, email=email, password="s3cret-pass")


def make_document(user, title="doc.txt"):
    return Document.objects.create(
        title=title,
        source_type=Document.SourceType.TEXT,
        sha256=uuid.uuid4().hex + uuid.uuid4().hex,
        status=Document.Status.INDEXED,
        uploaded_by=user,
    )


class DocumentOwnershipTests(TestCase):
    def setUp(self):
        self.alice = make_user("alice@example.com")
        self.bob = make_user("bob@example.com")
        self.bob_doc = make_document(self.bob, "bob.txt")
        self.client = Client()
        self.client.login(username="alice@example.com", password="s3cret-pass")

    def test_list_shows_only_own_documents(self):
        make_document(self.alice, "alice.txt")
        titles = [d["title"] for d in self.client.get("/api/documents/").json()]
        self.assertEqual(titles, ["alice.txt"])

    def test_cannot_open_or_delete_someone_elses_document(self):
        self.assertEqual(self.client.get(f"/api/documents/{self.bob_doc.pk}/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/documents/{self.bob_doc.pk}/").status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.bob_doc.pk).exists())

    @patch("documents.views.ingest_document.delay")
    def test_same_file_uploaded_by_two_users_gives_each_their_own_document(self, mock_delay):
        def upload(client):
            f = SimpleUploadedFile("same.txt", b"identical content", content_type="text/plain")
            return client.post("/api/documents/upload/files/", {"files": [f]}).json()[0]

        first = upload(self.client)
        bob_client = Client()
        bob_client.login(username="bob@example.com", password="s3cret-pass")
        second = upload(bob_client)
        again = upload(self.client)

        self.assertFalse(first["duplicate"])
        self.assertFalse(second["duplicate"])
        self.assertNotEqual(first["document"]["id"], second["document"]["id"])
        self.assertTrue(again["duplicate"])
        self.assertEqual(again["document"]["id"], first["document"]["id"])

    def test_chat_search_scope_is_own_documents_only(self):
        alice_doc = make_document(self.alice)
        self.assertEqual(allowed_document_ids(self.alice), [alice_doc.pk])
        self.assertEqual(allowed_document_ids(self.alice, [self.bob_doc.pk]), [])

    def test_user_with_no_documents_searches_nothing(self):
        self.assertEqual(allowed_document_ids(self.alice), [])
        self.assertEqual(get_all_chunks(document_ids=[]), [])
