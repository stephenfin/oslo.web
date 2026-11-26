===========
URL mapping
===========

The :mod:`oslo_web.wsgi.urlmap` module mounts WSGI applications at URL
prefixes, optionally restricted by domain and port. It is ported from
Paste's URL mapper and does not require Paste.

For example, a PasteDeploy configuration can use the composite factory:

.. code-block:: ini

   [composite:main]
   use = call:oslo_web.wsgi.urlmap:urlmap_factory
   / = versions
   /v1 = api

Here, `versions` and `api` are application or pipeline sections in the
same configuration file. The optional `not_found_app` setting names the
application to invoke when no mapping matches; otherwise a 404 is returned.

Applications are matched by path prefix, longest first within a domain.
Explicit domains take precedence over domain-independent mappings.
The mapper adjusts `SCRIPT_NAME` and `PATH_INFO` before calling the
selected application.

Migrating from Paste
--------------------

Replace `egg:Paste#urlmap` with
`call:oslo_web.wsgi.urlmap:urlmap_factory` in composite sections.
Python users can replace `paste.urlmap` with `oslo_web.wsgi.urlmap`.
The `URLMap` class, `urlmap_factory`, and `parse_path_expression` are
available, including the attributes and methods used by existing service
subclasses. The legacy `paste.urlmap_object` WSGI environment key is retained.

404 responses use WebOb rather than Paste, so their representation can differ.
Paste's `PathProxyURLMap` wrapper is not included.

See :doc:`/reference/urlmap` for the API reference.
