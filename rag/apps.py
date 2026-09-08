from django.apps import AppConfig


class RagConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'rag'

    def ready(self):
        # Warm the embedding + reranker model caches the moment a Celery
        # worker process boots, so the first ingestion/retrieval task
        # doesn't pay the (multi-second) model load cost. Safe to import
        # here since celery is always installed; the signal simply never
        # fires outside a worker process.
        #
        # Retrieval (embed_query + rerank) also runs synchronously in the
        # Django web process for chat requests. There's no equivalent
        # "web worker boot" signal to hook cheaply without slowing down
        # every `manage.py` invocation (migrate, makemigrations, shell...),
        # so that process relies on the plain lru_cache in rag.embeddings /
        # rag.reranker instead: the first real request pays the cold-load
        # cost once. Deployment notes cover pre-warming this properly via
        # gunicorn's --preload + post_fork hook.
        from celery.signals import worker_process_init

        def _warm_models(**kwargs):
            from rag.embeddings import get_embedding_model
            from rag.reranker import get_reranker

            get_embedding_model()
            get_reranker()

        worker_process_init.connect(_warm_models, weak=False)
