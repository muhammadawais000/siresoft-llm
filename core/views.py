from django.contrib import messages
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView


@method_decorator(ensure_csrf_cookie, name="dispatch")
class IndexView(TemplateView):
    """The single-page app shell. Everything else is driven client-side
    against the DRF/SSE API.

    ensure_csrf_cookie: the SPA's own POST/PUT/DELETE fetch calls (see
    static/js/app.js) read the csrftoken cookie themselves rather than
    templating a token in, since none of those calls originate from a
    server-rendered <form>. Without this decorator the cookie is only set
    once *something* calls get_token(request) (e.g. a template using
    {% csrf_token %}), which this template never does.
    """

    template_name = "index.html"

    def get_context_data(self, **kwargs):
        # The SPA doesn't render Django messages itself; hand them to
        # app.js, which shows them as toasts (e.g. the accounts app's
        # access-hours notice set at sign-in).
        context = super().get_context_data(**kwargs)
        context["initial_messages"] = [
            {"text": str(m), "type": "error" if m.level_tag == "error" else "info"}
            for m in messages.get_messages(self.request)
        ]
        return context
