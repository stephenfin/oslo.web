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

"""Action metadata for ordinary controller classes."""

from collections.abc import Callable
from typing import TypeVar

F = TypeVar('F', bound=Callable[..., object])


def action(name: str) -> Callable[[F], F]:
    """Mark a controller method as handling a named body action.

    The method is returned unchanged, with a ``wsgi_action`` attribute. No
    controller base class or metaclass is required. Use normal instance
    methods.
    """
    if not isinstance(name, str) or not name:
        raise ValueError('action name must be a nonempty string')

    def decorator(method: F) -> F:
        setattr(method, 'wsgi_action', name)
        return method

    return decorator


def collect_actions(controller: type[object]) -> dict[str, str]:
    """Map action names to effective method names, respecting Python's MRO.

    An override without a decorator removes the overridden method's action;
    redecorating it replaces the action name. Duplicate action names on
    distinct effective methods are ambiguous and rejected. Descriptors are not
    invoked. The returned mapping is a fresh snapshot, not shared mutable class
    state.
    """
    members: dict[str, object] = {}
    for base in reversed(controller.__mro__):
        members.update(vars(base))
    result: dict[str, str] = {}
    for method_name, method in members.items():
        if not callable(method):
            continue
        name = getattr(method, 'wsgi_action', None)
        if not isinstance(name, str) or not name:
            continue
        if name in result:
            raise ValueError(f'duplicate action name: {name!r}')
        result[name] = method_name
    return result
