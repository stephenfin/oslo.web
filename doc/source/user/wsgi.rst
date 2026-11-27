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
version negotiation, context/project checks, policy, versioned methods,
status/header decorators, and legacy extension processing are not supplied.
Adapters can implement service rules through these hooks. There is no
service-specific request cache, exception mapping, or context lookup.

See :doc:`/reference/wsgi` for the API reference.
