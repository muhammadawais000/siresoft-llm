from django.views.generic import TemplateView


class IndexView(TemplateView):
    """The single-page app shell. Everything else is driven client-side
    against the DRF/SSE API — this view has no context to build.
    """

    template_name = "index.html"
