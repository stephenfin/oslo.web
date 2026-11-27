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

Action metadata
---------------

.. autofunction:: oslo_web.wsgi.actions.action

.. autofunction:: oslo_web.wsgi.actions.collect_actions

Controller decorators
---------------------

.. autofunction:: oslo_web.wsgi.decorators.api_version

.. autofunction:: oslo_web.wsgi.decorators.response

.. autoclass:: oslo_web.wsgi.decorators.ResponseCodes

Loader exceptions
-----------------

.. autoexception:: oslo_web.wsgi.exceptions.ConfigNotFound

.. autoexception:: oslo_web.wsgi.exceptions.PasteAppNotFound
