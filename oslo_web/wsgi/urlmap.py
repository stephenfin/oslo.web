# Copyright (c) 2005 Ian Bicking and contributors
# Imported from Paste 3.11.0 (paste/urlmap.py).
#
# Copyright (c) 2006-2007 Ian Bicking and Contributors
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Map URL prefixes to WSGI applications.

Ported from ``paste.urlmap``. The ``paste.urlmap_object`` environment key
is retained for compatibility with existing applications and subclasses.
"""

from __future__ import annotations

from collections import abc
import html
import re
from typing import cast, Protocol
from wsgiref import types as wsgi_types

from webob import exc

__all__ = ['URLMap', 'parse_path_expression', 'urlmap_factory']


_NormalizedURL = tuple[str | None, str]
_URL = str | _NormalizedURL | list[str]
_Application = tuple[_NormalizedURL, wsgi_types.WSGIApplication]


class _AppLoader(Protocol):
    def get_app(
        self, name: str, global_conf: dict[str, str]
    ) -> wsgi_types.WSGIApplication: ...


def urlmap_factory(
    loader: _AppLoader, global_conf: dict[str, str], **local_conf: str
) -> URLMap:
    """Build a URL map from a PasteDeploy composite configuration.

    Mapping keys are path expressions and values are application names.
    ``not_found_app`` may name a fallback application in either the local
    or global configuration; the local setting takes precedence.
    """
    not_found_name: str | None
    if 'not_found_app' in local_conf:
        not_found_name = local_conf.pop('not_found_app')
    else:
        not_found_name = global_conf.get('not_found_app')
    not_found_app = None
    if not_found_name:
        not_found_app = loader.get_app(not_found_name, global_conf=global_conf)
    urlmap = URLMap(not_found_app=not_found_app)
    for path, app_name in local_conf.items():
        path = parse_path_expression(path)
        app = loader.get_app(app_name, global_conf=global_conf)
        urlmap[path] = app
    return urlmap


def parse_path_expression(path: str) -> str:
    """Parse a path expression into a URL suitable for :class:`URLMap`.

    Accepts expressions like 'domain foobar.com port 20 /' or
    just '/foobar' for a path alone.
    """
    parts = path.split()
    domain = port = parsed_path = None
    while parts:
        if parts[0] == 'domain':
            parts.pop(0)
            if not parts:
                raise ValueError(
                    "'domain' must be followed with a domain name"
                )
            if domain:
                raise ValueError("'domain' given twice")
            domain = parts.pop(0)
        elif parts[0] == 'port':
            parts.pop(0)
            if not parts:
                raise ValueError("'port' must be followed with a port number")
            if port:
                raise ValueError("'port' given twice")
            port = parts.pop(0)
        else:
            if parsed_path:
                raise ValueError(
                    f'more than one path given '
                    f'(have {parsed_path!r}, got {parts[0]!r})'
                )
            parsed_path = parts.pop(0)
    s = ''
    if domain:
        s = f'http://{domain}'
    if port:
        if not domain:
            raise ValueError('If you give a port, you must also give a domain')
        s += ':' + port
    if parsed_path:
        if s:
            s += '/'
        s += parsed_path
    return s


class URLMap(abc.MutableMapping[_URL, wsgi_types.WSGIApplication]):
    """Dispatch to WSGI applications by URL prefix.

    URLMap instances are dictionary-like objects that dispatch to one
    of several applications based on the URL.

    The dictionary keys are URLs to match (like
    ``PATH_INFO.startswith(url)``), and the values are applications to
    dispatch to.  URLs are matched most-specific-first, i.e., longest
    URL first.  The ``SCRIPT_NAME`` and ``PATH_INFO`` environmental
    variables are adjusted to indicate the new context.

    URLs can also include domains, like ``http://blah.com/foo``, or as
    tuples ``('blah.com', '/foo')``.  This will match domain names; without
    the ``http://domain`` or with a domain of ``None`` any domain will be
    matched (so long as no other explicit domain matches).
    """

    def __init__(
        self, not_found_app: wsgi_types.WSGIApplication | None = None
    ) -> None:
        self.applications: list[_Application] = []
        if not not_found_app:
            not_found_app = self.not_found_app
        self.not_found_application = not_found_app

    def __len__(self) -> int:
        return len(self.applications)

    def __iter__(self) -> abc.Iterator[_NormalizedURL]:
        for app_url, app in self.applications:
            yield app_url

    norm_url_re = re.compile('//+')
    domain_url_re = re.compile('^(http|https)://')

    def not_found_app(
        self,
        environ: wsgi_types.WSGIEnvironment,
        start_response: wsgi_types.StartResponse,
    ) -> abc.Iterable[bytes]:
        mapper = environ.get('paste.urlmap_object')
        if mapper:
            matches = [p for p, a in mapper.applications]
            extra = 'defined apps: {}'.format(',\n  '.join(map(repr, matches)))
        else:
            extra = ''
        extra += '\nSCRIPT_NAME: {!r}'.format(
            html.escape(environ.get('SCRIPT_NAME', ''))
        )
        extra += '\nPATH_INFO: {!r}'.format(
            html.escape(environ.get('PATH_INFO', ''))
        )
        extra += '\nHTTP_HOST: {!r}'.format(
            html.escape(environ.get('HTTP_HOST', ''))
        )
        app = exc.HTTPNotFound(comment=extra)
        return app(environ, start_response)

    def normalize_url(self, url: _URL, trim: bool = True) -> _NormalizedURL:
        if isinstance(url, (list, tuple)):
            domain = url[0]
            url = self.normalize_url(url[1])[1]
            return domain, url
        # Preserve the assertion expected by existing URLMap users.
        assert (  # noqa: S101
            not url or url.startswith('/') or self.domain_url_re.search(url)
        ), f'URL fragments must start with / or http:// (you gave {url!r})'
        match = self.domain_url_re.search(url)
        if match:
            url = url[match.end() :]
            if '/' in url:
                domain, url = url.split('/', 1)
                url = '/' + url
            else:
                domain, url = url, ''
        else:
            domain = None
        url = self.norm_url_re.sub('/', url)
        if trim:
            url = url.rstrip('/')
        return domain, url

    def sort_apps(self) -> None:
        """Sort applications by domain, then longest URL first."""

        def key(app_desc: _Application) -> tuple[str, int, str]:
            (domain, url), _app = app_desc
            # Make sure empty domains sort last and break length ties by URL.
            return domain or '\xff', -len(url), url

        self.applications.sort(key=key)

    def __setitem__(
        self, url: _URL, app: wsgi_types.WSGIApplication | None
    ) -> None:
        if app is None:
            try:
                del self[url]
            except KeyError:
                pass
            return
        dom_url = self.normalize_url(url)
        if dom_url in self:
            del self[dom_url]
        self.applications.append((dom_url, app))
        self.sort_apps()

    def __getitem__(self, url: _URL) -> wsgi_types.WSGIApplication:
        dom_url = self.normalize_url(url)
        for app_url, app in self.applications:
            if app_url == dom_url:
                return app
        raise KeyError(
            f'No application with the url {dom_url[1]!r} '
            f'(domain: {dom_url[0] or "*"!r}; '
            f'existing: {self.applications})'
        )

    def __delitem__(self, url: _URL) -> None:
        url = self.normalize_url(url)
        for app_url, app in self.applications:
            if app_url == url:
                self.applications.remove((app_url, app))
                break
        else:
            raise KeyError(f'No application with the url {url!r}')

    def keys(self) -> abc.KeysView[_URL]:
        """Return a live view of normalized keys in routing order."""
        # Supply a local docstring: the inherited one confuses Sphinx.
        return super().keys()

    def __call__(
        self,
        environ: wsgi_types.WSGIEnvironment,
        start_response: wsgi_types.StartResponse,
    ) -> abc.Iterable[bytes]:
        host = cast(
            str, environ.get('HTTP_HOST', environ.get('SERVER_NAME'))
        ).lower()
        if ':' in host:
            host, port = host.split(':', 1)
        else:
            if environ['wsgi.url_scheme'] == 'http':
                port = '80'
            else:
                port = '443'
        path_info = cast(str, environ.get('PATH_INFO'))
        path_info = self.normalize_url(path_info, False)[1]
        for (domain, app_url), app in self.applications:
            if domain and domain != host and domain != host + ':' + port:
                continue
            if path_info == app_url or path_info.startswith(app_url + '/'):
                environ['SCRIPT_NAME'] += app_url
                environ['PATH_INFO'] = path_info[len(app_url) :]
                return app(environ, start_response)
        environ['paste.urlmap_object'] = self
        return self.not_found_application(environ, start_response)
