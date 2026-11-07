"""A urllib redirect handler that never forwards credentials to another host."""

import urllib.parse
import urllib.request


class SameHostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to the host that was asked.

    CPython copies the request headers, Authorization included, to the redirect target. A token must not leave
    api.github.com because a proxy fault or an API change pointed the request somewhere else, so a redirect to another host is refused
    (it surfaces as an HTTPError for the original request).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)
