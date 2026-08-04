"""
Internal helper: a single decorator that replaces the

    try:
        ...
    except SomeDomainError as e:
        logger.error(...)
        raise
    except Exception as e:
        logger.exception(...)
        raise SomeDomainError(f"... failed: {e}") from e

pattern that used to be copy-pasted into almost every public method in this
package. Behaviour is unchanged, it is just no longer duplicated ~40 times.
"""

from __future__ import annotations

import functools
import logging
from typing import Callable, Tuple, Type, TypeVar

F = TypeVar("F", bound=Callable)


def wrap_errors(
    wrap_as: Type[Exception],
    *,
    passthrough: Tuple[Type[Exception], ...] = (),
) -> Callable[[F], F]:
    """
    Decorate a method so that:

    - exceptions whose type is listed in ``passthrough`` (typically the
      package's own domain exceptions, already raised with a useful
      message deeper in the call) are logged at ERROR level and
      re-raised unchanged;
    - any other exception is logged with a full traceback and re-raised
      wrapped in ``wrap_as``, with the original exception preserved via
      ``raise ... from e``.

    Works for both sync and async callables.
    """

    def decorator(func: F) -> F:
        logger = logging.getLogger(func.__module__)

        if _is_coroutine_function(func):

            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                try:
                    return await func(*args, **kwargs)
                except passthrough as e:
                    logger.error("[%s] failed: %s", func.__qualname__, e)
                    raise
                except Exception as e:
                    logger.exception("[%s] unexpected error", func.__qualname__)
                    raise wrap_as(f"{func.__name__} failed: {e}") from e

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except passthrough as e:
                logger.error("[%s] failed: %s", func.__qualname__, e)
                raise
            except Exception as e:
                logger.exception("[%s] unexpected error", func.__qualname__)
                raise wrap_as(f"{func.__name__} failed: {e}") from e

        return wrapper  # type: ignore[return-value]

    return decorator


def _is_coroutine_function(func: Callable) -> bool:
    import inspect

    return inspect.iscoroutinefunction(func)
