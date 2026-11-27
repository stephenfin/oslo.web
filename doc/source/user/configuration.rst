=============
Configuration
=============

oslo.web uses oslo.config for its configuration options. All options belong
to the ``[oslo_web]`` group in the consuming service's configuration file.

Option registration
-------------------

Call ``oslo_web.opts.register_wsgi_opts(conf)`` before parsing the service's
configuration. This registers both the PasteDeploy loader option and the
response-validation option. The loader also registers its own option when
constructed, but early registration allows applications to configure it before
constructing the loader.

The response-validation decorators currently read the global
``oslo_config.cfg.CONF`` object. Applications using them should register
options on that object. The loader instead uses the configuration object
passed to its constructor.

Generating sample configuration
--------------------------------

The ``oslo.web`` entry point in the ``oslo.config.opts`` namespace exposes all
of the library's configuration options. To generate a sample::

    oslo-config-generator --namespace oslo.web --output-file oslo-web.conf.sample

Services can add ``oslo.web`` to the namespaces in their existing
oslo-config-generator configuration. This discovers option definitions; it does
not register options on the application's runtime configuration object.

See :doc:`/reference/configuration` for the available options.
