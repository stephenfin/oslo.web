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

"""A WebOb controller pipeline, independent of service-specific API rules."""

from collections.abc import Callable, Mapping
import json
from typing import cast

import webob
import webob.dec
import webob.exc

from oslo_web.wsgi import actions
from oslo_web.wsgi import decorators


class Resource:
    """Dispatch Routes arguments to a controller using plain WebOb objects.

    Controller methods receive ``req`` and route keyword arguments. POST,
    PUT, and PATCH methods also receive a deserialized ``body`` argument.
    Return a JSON-serializable value, ``None`` for an empty response,
    or a WebOb response for explicit status, headers, or streaming content.
    Generated responses default to HTTP 200 unless the method is decorated
    with :func:`oslo_web.wsgi.decorators.response`.

    Override the individual pipeline methods to implement service conventions.
    This class supplies neither version negotiation nor policy checks,
    legacy extension processing, or a service-specific fault body.
    """

    body_methods = frozenset({'POST', 'PUT', 'PATCH'})

    def __init__(self, controller: object) -> None:
        self.controller = controller
        self.actions = actions.collect_actions(type(controller))

    @webob.dec.wsgify(RequestClass=webob.Request)
    def __call__(self, request: webob.Request) -> webob.Response:
        try:
            self.process_request(request)
            action_args = self.get_action_args(request.environ)
            action = action_args.pop('action', None)
            content_type = self.negotiate_content_type(request)
            body_arguments = self.get_body_arguments(request)
            method = self.resolve_method(request, action, body_arguments)
            action_args.update(body_arguments)
            result = self.dispatch(method, request, action_args)
            response = self.serialize(request, result, content_type)
            if not isinstance(result, webob.Response):
                status = self.get_response_status(request, method)
                if status is not None:
                    response.status_int = status
        except Exception as error:
            response = self.handle_exception(request, error)
        # Finalize both successful responses and handled errors. Failures in
        # the finalizer itself propagate rather than recursively handling them.
        return self.process_response(request, response)

    def process_request(self, request: webob.Request) -> None:
        """Prepare the request, e.g. negotiate a service's API version."""

    def get_action_args(
        self, environ: Mapping[str, object]
    ) -> dict[str, object]:
        """Copy Routes arguments, omitting controller and format entries."""
        routing = environ.get('wsgiorg.routing_args')
        if not isinstance(routing, tuple) or len(routing) != 2:
            return {}
        if not isinstance(routing[1], dict):
            return {}
        args = dict(routing[1])
        args.pop('controller', None)
        args.pop('format', None)
        return args

    def negotiate_content_type(self, request: webob.Request) -> str:
        """Select JSON, raising HTTP 406 if explicitly excluded by Accept."""
        if not request.accept.acceptable_offers(['application/json']):
            raise webob.exc.HTTPNotAcceptable()
        return 'application/json'

    def get_method(
        self, request: webob.Request, action: object
    ) -> Callable[..., object]:
        """Find a public controller method, or raise HTTP 404.

        Override for version-dependent or service-specific method lookup.
        """
        if not isinstance(action, str) or action.startswith('_'):
            raise webob.exc.HTTPNotFound()
        method = getattr(self.controller, action, None)
        if not callable(method):
            raise webob.exc.HTTPNotFound()
        return cast(Callable[..., object], method)

    def resolve_method(
        self,
        request: webob.Request,
        action: object,
        body_arguments: Mapping[str, object],
    ) -> Callable[..., object]:
        """Prefer a routed method, falling back to registered body actions.

        Only a route targeting ``action`` can use body-action fallback, and
        only when the controller has registered actions. The decoded body is
        shared with dispatch rather than deserialized a second time.
        """
        try:
            return self.get_method(request, action)
        except webob.exc.HTTPNotFound:
            if action != 'action' or not self.actions:
                raise
        name = self.get_action_name(request, body_arguments.get('body'))
        method_name = self.actions.get(name)
        if method_name is None:
            raise webob.exc.HTTPBadRequest(explanation='Unknown body action')
        # Explicit registration allows private implementation method names.
        method = getattr(self.controller, method_name)
        if not callable(method):
            raise webob.exc.HTTPNotFound()
        return cast(Callable[..., object], method)

    def get_action_name(self, request: webob.Request, body: object) -> str:
        """Extract the sole key of a JSON object, or raise HTTP 400.

        Override for alternative action envelopes. The handler receives the
        full body unchanged, not just the selected action's value.
        """
        if not isinstance(body, dict) or len(body) != 1:
            raise webob.exc.HTTPBadRequest(
                explanation='Action body must be an object with one key'
            )
        name = next(iter(body))
        if not isinstance(name, str):
            raise webob.exc.HTTPBadRequest(explanation='Invalid action name')
        return name

    def get_body_arguments(self, request: webob.Request) -> dict[str, object]:
        """Extract body arguments only for ``body_methods``.

        An empty body becomes ``None``. Nonempty bodies default to JSON when
        Content-Type is absent; an explicit non-JSON media type raises HTTP
        415. Override to support other media types or legacy body conventions.
        """
        if request.method not in self.body_methods:
            return {}
        body = self.get_body(request)
        if not body:
            return {'body': None}
        if request.content_type not in (None, '', 'application/json'):
            raise webob.exc.HTTPUnsupportedMediaType()
        return {'body': self.deserialize(request, body)}

    def get_body(self, request: webob.Request) -> bytes:
        """Read the request body. Override for alternative body extraction."""
        return request.body

    def deserialize(self, request: webob.Request, body: bytes) -> object:
        """Decode UTF-8 JSON, translating malformed input to HTTP 400."""
        try:
            return json.loads(body.decode('utf-8'))
        except (ValueError, UnicodeError) as error:
            raise webob.exc.HTTPBadRequest(
                explanation='Malformed JSON request body'
            ) from error

    def dispatch(
        self,
        method: Callable[..., object],
        request: webob.Request,
        action_args: dict[str, object],
    ) -> object:
        """Invoke the controller; implementation errors are not HTTP 400s."""
        return method(req=request, **action_args)

    def get_response_status(
        self, request: webob.Request, method: Callable[..., object]
    ) -> int | None:
        """Select a default status, or leave serialization unchanged.

        Called only for controller results other than explicit WebOb responses.
        Select version-dependent metadata using its request version getter.
        Override for service conventions. The status is applied after
        serialization and before finalisation.
        """
        codes = getattr(method, 'wsgi_codes', None)
        if isinstance(codes, decorators.ResponseCodes):
            return codes(request)
        code = getattr(method, 'wsgi_code', None)
        if isinstance(code, int) and not isinstance(code, bool):
            return code
        return None

    def serialize(
        self, request: webob.Request, result: object, content_type: str
    ) -> webob.Response:
        """Pass through WebOb responses or encode a JSON/empty body.

        Existing responses are not inspected or consumed, so binary and
        streaming responses retain their status, headers, and body iterator.
        Override for service-specific JSON encoders or response wrappers.
        """
        if isinstance(result, webob.Response):
            return result
        body = (
            json.dumps(result).encode('utf-8') if result is not None else b''
        )
        return webob.Response(body=body, content_type=content_type)

    def handle_exception(
        self, request: webob.Request, error: Exception
    ) -> webob.Response:
        """Return HTTP exceptions unchanged and re-raise unexpected failures.

        Override to translate service exceptions and render fault envelopes.
        Unexpected failures are left to the application's outer middleware.
        """
        if isinstance(error, webob.exc.WSGIHTTPException):
            return error
        raise error

    def process_response(
        self, request: webob.Request, response: webob.Response
    ) -> webob.Response:
        """Finalize success and handled-error responses, e.g. add headers."""
        return response
