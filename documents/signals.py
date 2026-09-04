"""Keeps Qdrant in sync with Document deletion regardless of how a
Document row is deleted -- the DRF viewset, the admin, a management
command, a future cascade -- rather than relying on every individual call
site to remember to clean up the vector store itself.

pre_delete (not post_delete): the document's id is still guaranteed valid
at this point; nothing here depends on the row still existing afterwards.
"""

from django.db.models.signals import pre_delete
from django.dispatch import receiver

from documents.models import Document
from rag.vector_store import delete_by_document


@receiver(pre_delete, sender=Document)
def delete_document_vectors(sender, instance, **kwargs):
    delete_by_document(instance.id)
