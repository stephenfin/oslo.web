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
Those responsibilities remain with the consuming application.

See :doc:`/reference/wsgi` for the API reference.
