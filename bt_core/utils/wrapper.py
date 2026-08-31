#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sat Feb 16 13:56:19 2019

@author: python
"""
import cProfile
import pstats
import io
import sys
import weakref
import contextlib
import logging
import time
import signal
import warnings
import threading
from functools import wraps
from contextlib import contextmanager


def singleton(cls):

    instance = weakref.WeakValueDictionary()
    def _singleton(*args,**kwargs):
        with threading.Lock() as lock:
            if cls not in instance:
                instance[cls] = cls(*args,**kwargs)
        return instance[cls]
    return _singleton

def deprecated(msg=None, stacklevel=2):
    """
    Used to mark a function as deprecated.

    Parameters
    ----------
    msg : str
        The message to display in the deprecation warning.
    stacklevel : int
        How far up the stack the warning needs to go, before
        showing the relevant calling lines.

    Examples
    --------
    @deprecated(msg='function_a is deprecated! Use function_b instead.')
    def function_a(*args, **kwargs):
    """
    def deprecated_dec(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            warnings.warn(
                msg or "Function %s is deprecated." % fn.__name__,
                category=DeprecationWarning,
                stacklevel=stacklevel
            )
            return fn(*args, **kwargs)
        return wrapper
    return deprecated_dec

def _deprecated_getitem_method(name, attrs):
    """Create a deprecated ``__getitem__`` method that tells users to use
    getattr instead.

    Parameters
    ----------
    name : str
        The name of the object in the warning message.
    attrs : iterable[str]
        The set of allowed attributes.

    Returns
    -------
    __getitem__ : callable[any, str]
        The ``__getitem__`` method to put in the class dict.
    """
    attrs = frozenset(attrs)
    msg = (
        "'{name}[{attr!r}]' is deprecated, please use"
        " '{name}.{attr}' instead"
    )

    def __getitem__(self, key):
        """``__getitem__`` is deprecated, please use attribute access instead.
        """
        warnings.warn(msg.format(name=name, attr=key), DeprecationWarning, stacklevel=2)
        if key in attrs:
            return getattr(self, key)
        raise KeyError(key)

    return __getitem__


class Deprecated(object):

    def __init__(self, tip_info=''):
        self.tip_info = tip_info

    def __call__(self, obj):
        if isinstance(obj, object):
            return self._decorate_class(obj)
        else:
            return self._decorate_fun(obj)

    def _decorate_class(self, cls):

        msg = "class {} is deprecated".format(cls.__name__)
        if self.tip_info:
            msg += "; {}".format(self.tip_info)
        init = cls.__init__

        def wrapped(*args, **kwargs):
            warnings.warn(msg, category=DeprecationWarning)
            return init(*args, **kwargs)

        cls.__init__ = wrapped

        wrapped.__name__ = '__init__'
        wrapped.__doc__ = self._update_doc(init.__doc__)
        wrapped.deprecated_original = init
        return cls

    def _decorate_fun(self, fun):
        msg = "func {} is deprecated".format(fun.__name__)
        if self.tip_info:
            msg += "; {}".format(self.tip_info)

        def wrapped(*args, **kwargs):
            warnings.warn(msg, category=DeprecationWarning)
            return fun(*args, **kwargs)

        wrapped.__name__ = fun.__name__
        wrapped.__dict__ = fun.__dict__
        wrapped.__doc__ = self._update_doc(fun.__doc__)
        return wrapped

    def _update_doc(self, func_doc):
        deprecated_doc = "Deprecated"
        if self.tip_info:
            deprecated_doc = "{}: {}".format(deprecated_doc, self.tip_info)
        if func_doc:
            func_doc = "{}\n{}".format(deprecated_doc, func_doc)
        return func_doc

def warnings_filter(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        warnings.simplefilter('ignore')
        ret = func(*args, **kwargs)
        if not ABuEnv.g_ignore_all_warnings:
            warnings.simplefilter('default')
        return ret
    return wrapper

def catch_error(return_val=None, log=True):
    
    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                logging.exception(e) if log else logging.debug(e)
                return return_val
        return wrapper
    return decorate

def consume_time(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        end_time = time.time()
        print('{} cost {}s'.format(func.__name__, round(end_time - start_time, 3)))
        return result
    return wrapper

def empty_wrapper(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper

def empty_wrapper_with_params(*p_args, **p_kwargs): # noinspection PyUnusedLocal

    def decorate(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)
        return wrapper
    return decorate

def except_debug(func):

    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            print(e)
            return func(*args, **kwargs)
    return wrapper

def require_not_initialized(exception):
    """
    Decorator for API methods that should only be called during or before
    TradingAlgorithm.initialize.  `exception` will be raised if the method is
    called after initialize.

    Examples
    --------
    @require_not_initialized(SomeException("Don't do that!"))
    def method(self):
        # Do stuff that should only be allowed during initialize.
    """
    def decorator(method):
        @wraps(method)
        def wrapped_method(self, *args, **kwargs):
            if self.initialized: # not self.initialized /  
                raise exception
            return method(self, *args, **kwargs)
        return wrapped_method
    return decorator

def valid_check(func):

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        if self.valid:
            return func(self, *args, **kwargs)
        else:
            logging.info('metric input is invalid or zero order gen!')
    return wrapper

def _validate_type(_type=(list,tuple)):

    def decorate(func):
        def wrap(*args):
            res = func(*args)
            if not isinstance(res, _type):
                raise TypeError('can not algorithm type:%s' % _type)
            return res
        return wrap
    return decorate

def remove_na(f):
    @wraps(f)
    def wrapper(*args):
        result = f(*args)
        if isinstance(result, (pd.DataFrame, pd.Series)):
            result.dropna(inplace=True)
        return result
    return wrapper

def _make_unsupported_method(name):
    def method(*args, **kwargs):
        raise NotImplementedError(
            "Method %s is not supported on LabelArrays." % name
        )
    method.__name__ = name
    method.__doc__ = "Unsupported LabelArray Method: %s" % name
    return method

def api_method(f): # patch

    @wraps(f)
    def wrapped(*args, **kwargs):
        # Get the instance and call the method
        algo_instance = get_algo_instance()
        if algo_instance is None:
            raise RuntimeError(
                'api method %s'
                % f.__name__
            )
        return getattr(algo_instance, f.__name__)(*args, **kwargs)
    return wrapped

def profile(func):

    @wraps(func)
    async def wrapper(*args, **kwargs):  # 改为 async if needed
        if should_profile():  
            pr = cProfile.Profile()
            pr.enable()
            try:
                result = await func(*args, **kwargs) 
            finally:
                pr.disable()
                s = io.StringIO()
                ps = pstats.Stats(pr, stream=s).sort_stats('cumulative')
                ps.print_stats(30)  
                print(s.getvalue())
                # ps.dump_stats(f"profile_{time.time()}.prof")
            return result
        else:
            return await func(*args, **kwargs)
    return wrapper

def coerce_numbers_to_my_dtype(f):
    """
    A decorator for methods whose signature is f(self, other) that coerces
    ``other`` to ``self.dtype``.

    This is used to make comparison operations between numbers and `Factor`
    instances work independently of whether the user supplies a float or
    integer literal.

    For example, if I write::

        my_filter = my_factor > 3

    my_factor probably has dtype float64, but 3 is an int, so we want to coerce
    to float64 before doing the comparison.
    """
    @wraps(f)
    def method(self, other):
        if isinstance(other, Number):
            other = coerce_to_dtype(self.dtype, other)
        return f(self, other)
    return method

def optionally(preprocessor):
    """Modify a preprocessor to explicitly allow `None`.

    Parameters
    ----------
    preprocessor : callable[callable, str, any -> any]
        A preprocessor to delegate to when `arg is not None`.

    Returns
    -------
    optional_preprocessor : callable[callable, str, any -> any]
        A preprocessor that delegates to `preprocessor` when `arg is not None`.

    Examples
    --------
    >>> def preprocessor(func, argname, arg):
    ...     if not isinstance(arg, int):
    ...         raise TypeError('arg must be int')
    ...     return arg
    ...
    >>> @preprocess(a=optionally(preprocessor))
    ... def f(a):
    ...     return a
    ...
    >>> f(1)  # call with int
    1
    >>> f('a')  # call with not int
    Traceback (most recent call last):
       ...
    TypeError: arg must be int
    >>> f(None) is None  # call with explicit None
    True
    """
    @wraps(preprocessor)
    def wrapper(func, argname, arg):
        return arg if arg is None else preprocessor(func, argname, arg)

    return wrapper


def ensure_upper_case(func, argname, arg):
    if isinstance(arg, string_types):
        return arg.upper()
    else:
        raise TypeError(
            "{0}() expected argument '{1}' to"
            " be a string, but got {2} instead.".format(
                func.__name__,
                argname,
                arg,
            ),
        )

def watchdog(seconds=600):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            def _handler(signum, frame):
                raise TimeoutError(f"{func.__name__} exceeded {seconds}s")
            old = signal.signal(signal.SIGALRM, _handler)
            signal.alarm(seconds)
            try:
                return func(*args, **kwargs)
            finally:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old)
        return wrapper
    return decorator
