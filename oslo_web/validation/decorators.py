# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

"""Request Body validating middleware."""

from collections.abc import Callable
import functools
import inspect
import sys
from typing import Any, ParamSpec, TypeVar, cast

import microversion_parse
from oslo_config import cfg
from oslo_log import log
from oslo_serialization import jsonutils
import webob
import webob.exc

from oslo_web import microversion
from oslo_web.validation import exceptions
from oslo_web.validation import validators

if sys.version_info < (3, 15):
    from typing_extensions import sentinel

CONF = cfg.CONF
LOG = log.getLogger(__name__)

P = ParamSpec('P')
R = TypeVar('R')

T = TypeVar('T')


def validated(cls: T) -> T:
    setattr(cls, '_validated', True)
    return cls


def _version_range(
    minimum: microversion_parse.Version | None,
    maximum: microversion_parse.Version | None,
) -> microversion_parse.VersionRange | None:
    if minimum is None and maximum is None:
        return None
    return microversion_parse.VersionRange(minimum, maximum)


class Schemas:
    """A version-aware schema container with contiguous coverage."""

    def __init__(self) -> None:
        self._schemas = microversion.VersionedRegistry[dict[str, object]]()

    def add_schema(
        self,
        schema: dict[str, object],
        min_version: microversion_parse.Version | None,
        max_version: microversion_parse.Version | None,
    ) -> None:
        try:
            self._schemas.add(schema, min_version, max_version)
        except microversion.CoverageError as exc:
            raise RuntimeError(str(exc)) from exc

    def validate_schemas(self) -> None:
        """Ensure registered ranges are contiguous and do not overlap."""
        try:
            self._schemas.validate()
        except microversion.CoverageError as exc:
            raise RuntimeError(str(exc)) from exc

    def __call__(
        self, version: microversion_parse.Version
    ) -> dict[str, object] | None:
        """Return the schema applicable to the requested API version."""
        return self._schemas.get(version)


def _matches_version(
    bounds: microversion_parse.VersionRange | None,
    version_getter: Callable[..., microversion_parse.Version] | None,
    args: tuple[object, ...],
    kwargs: dict[str, Any],
) -> bool:
    if bounds is None:
        return True
    if version_getter is None:
        raise ValueError('versioned schemas require a version_getter')
    return version_getter(*args, **kwargs) in bounds


def _schema_validator(
    schema: dict[str, Any],
    target: object,
) -> None:
    """A helper method to execute JSON-Schema Validation.

    The decorators check version applicability before extracting the target.
    A validation failure raises ``ValidationError``.

    :param schema: The JSON Schema schema used to validate the target.
    :param target: The target to be validated by the schema.
    :returns: None.
    :raises: ``ValidationError`` if validation fails.
    """
    schema_validator = validators.SchemaValidator(schema)
    schema_validator.validate(target)


def request_query_schema(
    schema: dict[str, Any],
    min_version: microversion_parse.Version | None = None,
    max_version: microversion_parse.Version | None = None,
    *,
    version_getter: Callable[..., microversion_parse.Version] | None = None,
    query_getter: Callable[..., object] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator for registering a request query string schema on API methods.

    ``schema`` will be used for validating request query strings just before
    the API method is executed. By default, extract ``req.GET`` (or
    ``request.GET``) as a dict of lists, preserving repeated values.
    ``query_getter`` can receive the method arguments and return a different
    framework's query representation.

    :param schema: The JSON Schema schema used to validate the target.
    :param min_version: A version indicating the minimum API version
        ``schema`` applies against.
    :param max_version: A version indicating the maximum API version
        ``schema`` applies against.
    :param version_getter: Callback receiving the decorated method's arguments
        and returning a microversion_parse.Version. Required for versioned
        schemas.
    """

    if (min_version is not None or max_version is not None) and (
        version_getter is None
    ):
        raise ValueError('versioned schemas require a version_getter')

    bounds = _version_range(min_version, max_version)

    def add_validator(func: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if not _matches_version(bounds, version_getter, args, kwargs):
                return func(*args, **kwargs)
            try:
                if query_getter is not None:
                    query = query_getter(*args, **kwargs)
                else:
                    bound = signature.bind(*args, **kwargs)
                    request = bound.arguments.get('req')
                    if request is None:
                        request = bound.arguments.get('request')
                    if request is None:
                        request = kwargs.get('req', kwargs.get('request'))
                    if request is None:
                        raise ValueError(
                            'query validation requires req/request or '
                            'a query_getter'
                        )
                    query = cast(webob.Request, request).GET.dict_of_lists()
            except UnicodeDecodeError as exc:
                raise exceptions.ValidationError(
                    'Query string is not UTF-8 encoded'
                ) from exc
            _schema_validator(schema, query)
            return func(*args, **kwargs)

        # we need to use (get|has|set)attr since these are dynamically set on
        # functions
        if not hasattr(wrapper, 'request_query_schemas'):
            setattr(wrapper, 'request_query_schemas', Schemas())

        getattr(wrapper, 'request_query_schemas').add_schema(
            schema, min_version, max_version
        )

        return wrapper

    return add_validator


def request_body_schema(
    schema: dict[str, Any],
    min_version: microversion_parse.Version | None = None,
    max_version: microversion_parse.Version | None = None,
    *,
    version_getter: Callable[..., microversion_parse.Version] | None = None,
    body_getter: Callable[..., object] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator for registering a request body schema on API methods.

    ``schema`` will be used for validating the request body just before the API
    method is executed. By default, bind the method's ``body`` argument,
    including positional arguments and defaults. ``body_getter`` can receive
    the method arguments and extract a different framework's body.

    :param schema: The JSON Schema schema used to validate the target.
    :param min_version: A version indicating the minimum API version
        ``schema`` applies against.
    :param max_version: A version indicating the maximum API version
        ``schema`` applies against.
    :param version_getter: Callback receiving the decorated method's arguments
        and returning a microversion_parse.Version. Required for versioned
        schemas.
    """

    if (min_version is not None or max_version is not None) and (
        version_getter is None
    ):
        raise ValueError('versioned schemas require a version_getter')

    bounds = _version_range(min_version, max_version)

    def add_validator(func: Callable[P, R]) -> Callable[P, R]:
        signature = inspect.signature(func)

        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            if not _matches_version(bounds, version_getter, args, kwargs):
                return func(*args, **kwargs)
            if body_getter is not None:
                body = body_getter(*args, **kwargs)
            else:
                bound = signature.bind(*args, **kwargs)
                bound.apply_defaults()
                if 'body' in bound.arguments:
                    body = bound.arguments['body']
                elif 'body' in kwargs:
                    body = kwargs['body']
                else:
                    raise ValueError(
                        'body validation requires a body argument or '
                        'a body_getter'
                    )
            _schema_validator(schema, body)
            return func(*args, **kwargs)

        # we need to use (get|has|set)attr since these are dynamically set on
        # functions
        if not hasattr(wrapper, 'request_body_schemas'):
            setattr(wrapper, 'request_body_schemas', Schemas())

        getattr(wrapper, 'request_body_schemas').add_schema(
            schema, min_version, max_version
        )

        return wrapper

    return add_validator


_SKIP_VALIDATION = sentinel('_SKIP_VALIDATION')


def _response_body(response: object) -> object:
    # Flask handlers may return (body, status) or (body, status, headers).
    if isinstance(response, tuple) and len(response) in (2, 3):
        response = response[0]
    if isinstance(response, webob.Response):
        if response.content_type != 'application/json' and not (
            response.content_type or ''
        ).endswith('+json'):
            return _SKIP_VALIDATION
        # Do not consume streaming iterators to validate a response.
        if not isinstance(response.app_iter, (list, tuple)):
            return _SKIP_VALIDATION
        response = response.body
    else:
        # The Nova-style ResponseObject stores the representation in obj.
        response = getattr(response, 'obj', response)

    try:
        if isinstance(response, bytes):
            return jsonutils.loads(response) if response else None
        # Normalize datetime and other objects just as JSON serialization does.
        return jsonutils.loads(jsonutils.dumps(response))
    except (ValueError, TypeError, UnicodeError) as exc:
        raise exceptions.ValidationError(
            'Response body is not valid JSON'
        ) from exc


def response_body_schema(
    schema: dict[str, Any],
    min_version: microversion_parse.Version | None = None,
    max_version: microversion_parse.Version | None = None,
    *,
    version_getter: Callable[..., microversion_parse.Version] | None = None,
    response_getter: Callable[[R], object] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator for registering a response body schema on API methods.

    ``schema`` will be used for validating the response body just after the API
    method is executed. Supports JSON-compatible return values, objects with
    an ``obj`` payload, Flask tuples, and buffered WebOb JSON responses. Empty
    JSON responses validate as null. Streaming and non-JSON WebOb responses
    are left alone. ``response_getter`` receives the original return value
    and extracts a representation for other response types.

    :param schema: The JSON Schema schema used to validate the target.
    :param min_version: A version indicating the minimum API version
        ``schema`` applies against.
    :param max_version: A version indicating the maximum API version
        ``schema`` applies against.
    :param version_getter: Callback receiving the decorated method's arguments
        and returning a microversion_parse.Version. Required for versioned
        schemas.
    """

    if (min_version is not None or max_version is not None) and (
        version_getter is None
    ):
        raise ValueError('versioned schemas require a version_getter')

    bounds = _version_range(min_version, max_version)

    def add_validator(func: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(func)
        def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            response = func(*args, **kwargs)

            if CONF.oslo_web.response_validation == 'ignore':
                # don't waste our time checking anything if we're ignoring
                # schema errors
                return response

            if not _matches_version(bounds, version_getter, args, kwargs):
                return response

            try:
                payload = (
                    response_getter(response)
                    if response_getter is not None
                    else response
                )
                body = _response_body(payload)
                if body is _SKIP_VALIDATION:
                    return response
                _schema_validator(schema, body)
            except exceptions.ValidationError as exc:
                if CONF.oslo_web.response_validation == 'warn':
                    LOG.exception('Schema failed to validate')
                else:
                    raise webob.exc.HTTPInternalServerError(
                        'Response body failed schema validation'
                    ) from exc

            return response

        # we need to use (get|has|set)attr since these are dynamically set on
        # functions
        if not hasattr(wrapper, 'response_body_schemas'):
            setattr(wrapper, 'response_body_schemas', Schemas())

        getattr(wrapper, 'response_body_schemas').add_schema(
            schema, min_version, max_version
        )

        return wrapper

    return add_validator
