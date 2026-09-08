# Reverses 0002_remove_chunk_embedding.py -- moving dense/vector search
# back from Qdrant onto a pgvector column on Chunk itself.

import pgvector.django.indexes
import pgvector.django.vector
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('rag', '0002_remove_chunk_embedding'),
    ]

    operations = [
        migrations.AddField(
            model_name='chunk',
            name='embedding',
            field=pgvector.django.vector.VectorField(dimensions=768, null=True),
        ),
        migrations.AddIndex(
            model_name='chunk',
            index=pgvector.django.indexes.HnswIndex(
                ef_construction=64,
                fields=['embedding'],
                m=16,
                name='chunk_embedding_hnsw_idx',
                opclasses=['vector_cosine_ops'],
            ),
        ),
    ]
