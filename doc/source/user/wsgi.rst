==============
WSGI utilities
==============

oslo.web provides PasteDeploy application loading and Routes-based dispatch.
These utilities construct WSGI applications; they do not run an HTTP server.
Use a WSGI server such as uWSGI or Gunicorn to serve the resulting application.

Loading a PasteDeploy application
---------------------------------

Set the PasteDeploy configuration file in the service's oslo.config file:

.. code-block:: ini

    [oslo_web]
    api_paste_config = /etc/myservice/api-paste.ini

Register options before parsing the service configuration, then load the named
application:

.. code-block:: python

    from oslo_config import cfg

    from oslo_web import opts
    from oslo_web.wsgi import loader

    conf = cfg.ConfigOpts()
    opts.register_wsgi_opts(conf)
    conf(args=[], default_config_files=['/etc/myservice/myservice.conf'])

    application = loader.Loader(conf).load_app('api')

``api`` identifies an application or pipeline in the PasteDeploy file. For
example, an application section could use an application factory supplied by
the consuming service:

.. code-block:: ini

    [app:api]
    paste.app_factory = myservice.api:app_factory

The factory follows PasteDeploy's calling convention:
``app_factory(global_conf, **local_conf)`` returns a WSGI application.

Absolute paths are checked directly. Relative paths are resolved using
``conf.find_file()`` and its configuration-file search locations. A missing
configuration file raises
:class:`~oslo_web.wsgi.exceptions.ConfigNotFound`; a failed application lookup
raises :class:`~oslo_web.wsgi.exceptions.PasteAppNotFound`. Other PasteDeploy
errors, including import errors in application factories, can propagate.

Routing requests
----------------

:class:`~oslo_web.wsgi.router.Router` accepts a ``routes.Mapper`` whose routes
identify WSGI applications through their ``controller`` argument:

.. code-block:: python

    import routes
    import webob
    import webob.dec

    from oslo_web.wsgi import router

    @webob.dec.wsgify
    def health(request):
        return webob.Response(json_body={'status': 'ok'})

    mapper = routes.Mapper()
    mapper.connect('health', '/health', controller=health)
    application = router.Router(mapper)

    response = webob.Request.blank('/health').get_response(application)

An unmatched route returns HTTP 404. Matched route arguments are available in
``request.environ['wsgiorg.routing_args'][1]``. The router selects a WSGI
application; it does not itself dispatch controller methods based on an
``action`` argument, deserialize request bodies, or serialize return values.
Use a resource to add controller dispatch, or supply another WSGI application.

Controller resources
--------------------

:class:`~oslo_web.wsgi.resource.Resource` adds a small controller pipeline to
Routes dispatch. Requests and responses are ordinary WebOb objects; no custom
request subclass or response wrapper is required:

.. code-block:: python

    from oslo_web.wsgi import resource

    class Controller:
        def show(self, req, id):
            return {'item': {'id': id}}

        def create(self, req, body):
            return webob.Response(json_body=body, status=201)

    items = resource.Resource(Controller())
    mapper = routes.Mapper()
    mapper.connect(
        'show',
        '/items/{id}',
        controller=items,
        action='show',
        conditions={'method': ['GET']}
    )
    mapper.connect(
        'create',
        '/items',
        controller=items,
        action='create',
        conditions={'method': ['POST']}
    )
    application = router.Router(mapper)

The resource copies route arguments, removes ``controller``, ``format``, and
``action``, and passes the remaining arguments to the selected public method
alongside ``req``. A missing or non-callable method returns HTTP 404.

By default, POST, PUT, and PATCH requests receive a ``body`` argument. Empty
bodies become ``None``; nonempty bodies are decoded as UTF-8 JSON. Missing
Content-Type defaults to JSON, while an explicit non-JSON type on a nonempty
body returns HTTP 415. Malformed JSON returns HTTP 400. Other HTTP methods do
not read or deserialize the body. Accept negotiation selects
``application/json`` and returns HTTP 406 if it is excluded.

JSON-serializable return values are encoded as JSON with status 200. ``None``
returns an empty body with status 200, not 204. Return a ``webob.Response`` to
control status, headers, or content type. Existing responses pass through
without inspecting or consuming their body iterators, including binary and
streaming responses. The default Accept check still applies before dispatch;
override negotiation for resources serving other media types.

The pipeline provides the following override points, in execution order:

* ``process_request(request)`` for request preparation.
* ``get_action_args(environ)`` for route argument extraction.
* ``negotiate_content_type(request)`` for response media type selection.
* ``get_body_arguments(request)``, using ``get_body(request)`` and
  ``deserialize(request, body)``, for request body arguments.
* ``resolve_method(request, action, body_arguments)`` for method selection,
  using ``get_method(request, action)`` for routed methods and
  ``get_action_name(request, body)`` for body-action fallback.
* ``dispatch(method, request, action_args)`` for controller invocation.
* ``serialize(request, result, content_type)`` for response construction.
* ``get_response_status(request, method)`` for an optional default status on
  generated responses, applied after serialization.
* ``process_response(request, response)`` for response finalisation.

``handle_exception(request, error)`` handles errors from preparation through
serialization. By default, WebOb HTTP response exceptions pass through, using
WebOb's fault representation; unexpected errors propagate to outer middleware.
In particular, controller ``TypeError`` exceptions are not silently converted
into client errors. Override this hook to translate service exceptions or
render service-specific fault envelopes.

Response finalisation runs for successful responses and handled errors.
Exceptions from finalisation itself propagate without re-entering error
handling. Errors raised while iterating a streaming response occur outside the
pipeline and must be handled by the server or outer middleware.

Response status metadata
~~~~~~~~~~~~~~~~~~~~~~~~

Use :func:`~oslo_web.wsgi.decorators.response` to declare a method's default
HTTP status without wrapping the method:

.. code-block:: python

    from http import HTTPStatus

    from oslo_web.wsgi import decorators

    class Controller:
        @decorators.response(HTTPStatus.CREATED)
        def create(self, req, body):
            return {'item': body}

        @decorators.response(HTTPStatus.NO_CONTENT)
        def delete(self, req, id):
            pass

The decorator stores callable ``wsgi_codes`` metadata on the unchanged
function. A version-independent declaration also exposes ``wsgi_code`` for
simple adapters. It accepts integer HTTP codes from 100 to 599, including
``HTTPStatus`` members, but not booleans. Invalid codes raise ``ValueError``
when decorating the method. Repeated version-independent declarations are
rejected as overlapping coverage.

The selected method's status applies to JSON values and ``None`` results.
Undecorated methods retain the serializer's status (normally HTTP 200).
Explicitly returned WebOb responses, including streaming responses and HTTP
exceptions, retain their own status and headers. Raised exceptions also keep
the normal error-handling behavior. WebOb handles HEAD requests according to
its usual WSGI semantics. Return ``None`` for bodyless statuses such as HTTP
204; the decorator changes the status, not the serialized body.

Inherited methods retain their status metadata. An undecorated override does
not inherit the overridden method's metadata; a decorated override supplies
its own. The decorator works with ``@actions.action`` in either order.

``get_response_status(request, method)`` returns the selected method's code,
or ``None`` to leave serialization unchanged. It is called only for results
other than explicit WebOb responses. The code is applied after ``serialize``
and before ``process_response``, without changing the serializer hook's
signature. Adapters overriding serialization should override this hook too if
their serializer's status must take precedence over method metadata.

For version-dependent statuses, stack declarations over adjoining inclusive
ranges while retaining a single controller implementation:

.. code-block:: python

    import microversion_parse

    def response_version(req):
        return req.environ['compute.microversion']

    class Controller:
        @decorators.response(
            204,
            min_version=microversion_parse.Version(2, 20),
            version_getter=response_version,
        )
        @decorators.response(
            202,
            max_version=microversion_parse.Version(2, 19),
            version_getter=response_version,
        )
        def delete(self, req, id):
            pass

Unlike the ``api_version`` getter, this getter receives **only the WebOb
request**: status selection happens after controller invocation. All bounded
status declarations on a method must use the same getter callable. No getter
is required or called for a version-independent declaration.

The metadata uses :class:`~oslo_web.microversion.VersionedRegistry` directly.
Every registration must preserve contiguous, non-overlapping coverage:

* Bounds are ``microversion_parse.Version`` objects, not strings or integers.
* Successive ranges must meet at consecutive minor versions of the same major
  API version. Register adjoining ranges in ascending or descending order.
* Overlaps (including shared endpoints), gaps, and mixed version-independent
  and versioned declarations raise ``CoverageError`` at decoration time.
* Omitted bounds are open within the other bound's major version. No coverage
  outside the registered bounds is required.

If a negotiated version falls outside the registered coverage, response
construction raises HTTP 500 through the normal error/finalisation pipeline.
This indicates missing status configuration, not method unavailability; use
``@api_version`` to guard availability. Invalid getter values or getter errors
propagate. Explicit WebOb responses and raised exceptions bypass selection,
so their status does not depend on registered coverage.

Registrations are atomic and extending a wrapper's copied metadata does not
mutate the wrapped function's declarations. The decorator works in either
order with ``@api_version`` and ``@actions.action``.

Set dynamic headers on an explicit WebOb response or in ``process_response``;
there is no generic header decorator.

Version-gated methods
~~~~~~~~~~~~~~~~~~~~~

:func:`~oslo_web.wsgi.decorators.api_version` guards a single implementation
of a controller method. It checks availability when called, including direct
Python calls outside ``Resource``, and raises HTTP 404 outside its inclusive
version range. Services should normally provide a small wrapper to bind their
version getter and preferred bound syntax once, rather than repeating them on
every controller method:

.. code-block:: python

    import microversion_parse

    from oslo_web.wsgi import actions
    from oslo_web.wsgi import decorators

    def negotiated_version(controller, req, **kwargs):
        return req.environ['compute.microversion']

    # In the service's shared WSGI module:
    def api_version(min_version=None, max_version=None):
        return decorators.api_version(
            microversion_parse.parse_version_string(min_version)
            if min_version is not None else None,
            microversion_parse.parse_version_string(max_version)
            if max_version is not None else None,
            version_getter=negotiated_version,
        )

    # Controllers import and use the service's decorator:
    class Controller:
        @api_version('2.17')
        @decorators.response(202)
        @actions.action('trigger_crash_dump')
        def _trigger_crash_dump(self, req, id, body):
            pass

The getter receives exactly the decorated method's original arguments,
including ``self`` for an instance method. It should accept the controller's
positional and keyword calling conventions and any route/body arguments.
Here it reads the value set by ``microversion_parse.middleware`` for the
``compute`` service. Other adapters can read their own negotiated version
representation and convert it to ``microversion_parse.Version``.

The library's ``min_version`` and ``max_version`` parameters accept
``microversion_parse.Version`` objects, not strings or integers. The service
wrapper above converts convenient string bounds such as ``'2.17'`` before
calling the library decorator. It also accepts two bounds, for example
``@api_version('2.17', '2.30')``.

Bounds use inclusive ``VersionRange``
semantics: a missing bound is open within the supplied bound's major version.
Invalid or reversed ranges and cross-major ranges fail when decorating the
method. A getter is required if either bound is supplied; without bounds, the
method is unrestricted and the getter is not called.

The decorator preserves the signature and existing metadata using
``functools.wraps``, exposing ``min_version``, ``max_version``, and
``wsgi_version_range`` metadata. It works in either order with the action and
response decorators. Inheritance follows ordinary Python method lookup: an
inherited method retains its guard, while an undecorated override does not.
Out-of-range calls do not enter the method. Through ``Resource``, they use the
normal exception-handling and response-finalisation hooks, without acquiring
the method's success status.

Missing negotiated versions, invalid getter return values, and getter errors
are configuration/implementation errors: they propagate rather than being
silently converted to HTTP 404. This decorator does not negotiate versions,
parse request headers, or impose the service's globally supported range.

There is no alternate-implementation registry, metaclass, or dispatcher.
Repeated ``@api_version`` declarations on a method are rejected. Define one
implementation and put version-dependent behavior inside it; legacy services'
dispatcher-style decorators are intentionally not supported.

Body actions
~~~~~~~~~~~~

Register named body actions on ordinary controller instance methods with
:func:`~oslo_web.wsgi.actions.action`:

.. code-block:: python

    from oslo_web.wsgi import actions

    class Controller:
        @actions.action('restart')
        def _restart(self, req, id, body):
            return webob.Response(status=202)

    items = resource.Resource(Controller())
    mapper.connect(
        'item-action',
        '/items/{id}/action',
        controller=items,
        action='action',
        conditions={'method': ['POST']},
    )

A POST body such as ``{"restart": {"force": true}}`` selects ``_restart``.
The handler receives the complete decoded body, including the ``restart`` key,
plus the normal request and route arguments. The body is deserialized once.
Registered actions may use private implementation method names; such methods
remain inaccessible through direct route lookup.

Body-action lookup is a fallback only for routes targeting ``action``. An
explicit public controller method named ``action`` takes precedence. A
controller without registered body actions returns HTTP 404 if it has no such
method. When fallback is enabled, an empty body, a non-object body, an object
with other than one key, or an unknown action name returns HTTP 400. Malformed
JSON and unsupported media types retain the pipeline's HTTP 400/415 handling.
Restrict the route to POST in the mapper if other HTTP methods are not allowed.

The decorator attaches ``wsgi_action`` metadata without wrapping the method.
:func:`~oslo_web.wsgi.actions.collect_actions` collects a fresh mapping from
action names to effective method names, following Python's method resolution
order without evaluating properties. Inherited actions remain available unless
their methods are overridden. An undecorated override removes an action;
redecorating the override replaces its action name. Duplicate action names on
distinct effective methods are rejected with ``ValueError`` when constructing
the resource, rather than silently selecting an arbitrary handler.

``Resource.actions`` holds this metadata snapshot for each resource. Controller
classes should be fully defined before constructing their resources. No
controller base class or metaclass is required. Override ``resolve_method`` to
change fallback rules, or ``get_action_name`` for a different action envelope.

This is not yet a drop-in replacement for service resource frameworks. API
version negotiation, context/project checks, policy, and legacy extension
processing are not supplied.
Adapters can implement service rules through these hooks. There is no
service-specific request cache, exception mapping, or context lookup.

See :doc:`/reference/wsgi` for the API reference.
