# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Metadata and availability guards for WebOb controller methods."""

from collections.abc import Callable
import copy
import functools
from typing import ParamSpec, TypeVar

import microversion_parse
import webob
import webob.exc

from oslo_web import microversion

F = TypeVar('F', bound=Callable[..., object])
P = ParamSpec('P')
R = TypeVar('R')


class ResponseCodes:
    """Callable ``wsgi_codes`` metadata backed by ``VersionedRegistry``."""

    def __init__(self) -> None:
        self.registry = microversion.VersionedRegistry[int]()
        self.version_getter: (
            Callable[[webob.Request], microversion_parse.Version] | None
        ) = None
        self.default_code: int | None = None

    def add(
        self,
        code: int,
        minimum: microversion_parse.Version | None,
        maximum: microversion_parse.Version | None,
        version_getter: (
            Callable[[webob.Request], microversion_parse.Version] | None
        ),
    ) -> None:
        if (
            (minimum is not None or maximum is not None)
            and self.default_code is None
            and self.version_getter is not None
            and version_getter is not None
            and self.version_getter is not version_getter
        ):
            raise ValueError(
                'response declarations must share a version_getter'
            )
        self.registry.add(code, minimum, maximum)
        if minimum is None and maximum is None:
            self.default_code = code
        else:
            self.version_getter = version_getter

    def __call__(self, request: webob.Request) -> int:
        if self.default_code is not None:
            return self.default_code
        if self.version_getter is None:
            raise ValueError('versioned responses require a version_getter')
        code = self.registry.get(self.version_getter(request))
        if code is None:
            raise webob.exc.HTTPInternalServerError(
                explanation='No response status declared for this API version'
            )
        return code


def response(
    code: int,
    min_version: microversion_parse.Version | None = None,
    max_version: microversion_parse.Version | None = None,
    *,
    version_getter: (
        Callable[[webob.Request], microversion_parse.Version] | None
    ) = None,
) -> Callable[[F], F]:
    """Declare default HTTP statuses without wrapping the controller method.

    Integer codes from 100 to 599 (including ``http.HTTPStatus`` values) are
    accepted. Bounded declarations require a shared ``version_getter`` that
    receives the WebOb request and returns its negotiated version.

    Stack adjoining ranges in ascending or descending order. Registrations
    follow ``VersionedRegistry`` coverage rules: overlaps, gaps, and mixing
    version-independent and bounded declarations raise ``CoverageError``.
    Invalid bounds fail at decoration time. An unmatched negotiated version
    raises HTTP 500 during response construction. Explicit WebOb responses
    and exceptions bypass status selection.
    """
    if (
        isinstance(code, bool)
        or not isinstance(code, int)
        or not 100 <= code <= 599
    ):
        raise ValueError('response code must be an integer from 100 to 599')

    if (min_version is not None or max_version is not None) and (
        version_getter is None
    ):
        raise ValueError('versioned responses require a version_getter')
    # Validate bounds even before applying the returned decorator.
    if min_version is not None or max_version is not None:
        microversion_parse.VersionRange(min_version, max_version)

    def decorator(method: F) -> F:
        codes = getattr(method, 'wsgi_codes', None)
        if isinstance(codes, ResponseCodes):
            # wraps() copies function attributes by reference. Extend a fresh
            # snapshot so decorating a wrapper cannot mutate its source.
            codes = copy.copy(codes)
            codes.registry = copy.copy(codes.registry)
        else:
            codes = ResponseCodes()
        codes.add(code, min_version, max_version, version_getter)
        setattr(method, 'wsgi_codes', codes)
        if min_version is None and max_version is None:
            # Retain the simple metadata used by existing service adapters.
            setattr(method, 'wsgi_code', code)
        return method

    return decorator


def api_version(
    min_version: microversion_parse.Version | None = None,
    max_version: microversion_parse.Version | None = None,
    *,
    version_getter: Callable[..., microversion_parse.Version] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Guard a single controller implementation by inclusive version bounds.

    ``version_getter`` receives the decorated method's original arguments and
    must return a negotiated ``microversion_parse.Version``. It is required
    when either bound is supplied. Bounds use ``VersionRange`` semantics;
    omitted bounds are open within the supplied bound's major version.

    Out-of-range calls raise HTTP 404 before invoking the method. With no
    bounds, calls are unrestricted and the getter is not used. This decorator
    preserves method metadata but wraps invocation, including direct calls.
    Only one declaration per method is allowed; there is no handler registry,
    alternate-implementation dispatch, or coverage policy.
    """
    bounds = (
        microversion_parse.VersionRange(min_version, max_version)
        if min_version is not None or max_version is not None
        else None
    )
    if bounds is not None and version_getter is None:
        raise ValueError('versioned methods require a version_getter')

    def decorator(method: Callable[P, R]) -> Callable[P, R]:
        if hasattr(method, 'wsgi_version_range'):
            raise ValueError('only one api_version declaration is allowed')

        @functools.wraps(method)
        def wrapped(*args: P.args, **kwargs: P.kwargs) -> R:
            if bounds is not None and version_getter is not None:
                # Missing negotiation or invalid getter results are
                # configuration errors, not HTTP 404s.
                if version_getter(*args, **kwargs) not in bounds:
                    raise webob.exc.HTTPNotFound(
                        explanation='Method is unavailable at this API version'
                    )
            return method(*args, **kwargs)

        setattr(wrapped, 'min_version', min_version)
        setattr(wrapped, 'max_version', max_version)
        setattr(wrapped, 'wsgi_version_range', bounds)
        return wrapped

    return decorator
