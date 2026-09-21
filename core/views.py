from django.contrib.auth.views import LoginView
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from core.forms import EmailAuthenticationForm


@method_decorator(ensure_csrf_cookie, name="dispatch")
class IndexView(TemplateView):
    """The single-page app shell. Everything else is driven client-side
    against the DRF/SSE API — this view has no context to build.

    ensure_csrf_cookie: the SPA's own POST/PUT/DELETE fetch calls (see
    static/js/app.js) read the csrftoken cookie themselves rather than
    templating a token in, since none of those calls originate from a
    server-rendered <form>. Without this decorator the cookie is only set
    once *something* calls get_token(request) (e.g. a template using
    {% csrf_token %}), which this template never does.
    """

    template_name = "index.html"


class SiresoftLoginView(LoginView):
    template_name = "registration/login.html"
    form_class = EmailAuthenticationForm
    redirect_authenticated_user = True
