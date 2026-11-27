==============
WSGI utilities
==============

See :doc:`/user/wsgi` for usage guidance.

Loader
------

.. autoclass:: oslo_web.wsgi.loader.Loader
   :members:
   :special-members: __init__

Router
------

.. autoclass:: oslo_web.wsgi.router.Router
   :members:
   :special-members: __init__, __call__

Resource
--------

.. autoclass:: oslo_web.wsgi.resource.Resource
   :members:
   :special-members: __init__, __call__

Loader exceptions
-----------------

.. autoexception:: oslo_web.wsgi.exceptions.ConfigNotFound

.. autoexception:: oslo_web.wsgi.exceptions.PasteAppNotFound
