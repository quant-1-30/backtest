# !/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar 12 15:37:47 2019

@author: python
"""
import contextlib
import threading
from contextlib import ExitStack
from contextlib import contextmanager
from functools import wraps
from warnings import (
    catch_warnings,
    filterwarnings,
)

context = threading.local()


class Context(contextlib.ContextDecorator):

    def __init__(self, how_used):
        self.how_used = how_used

    def __enter__(self):
        # print(f'__enter__({self.how_used})')
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # print(f'__exit__({self.how_used})')
        return None


@contextlib.contextmanager
def make_context():
    # print("enter make_context")
    try:
        yield {}
    except RuntimeError as err:
        # print(f"{err=}")
        pass


class _ManagedCallbackContext(object):
    def __init__(self, pre, post, args, kwargs):
        self._pre = pre
        self._post = post
        self._args = args
        self._kwargs = kwargs

    def __enter__(self):
        return self._pre(*self._args, **self._kwargs)

    def __exit__(self, *excinfo):
        self._post(*self._args, **self._kwargs)


class WarningContext(object):
    """
    Re-usable contextmanager for contextually managing warnings.
    """
    def __init__(self, *warning_specs):
        self._warning_specs = warning_specs
        self._catchers = []

    def __enter__(self):
        catcher = catch_warnings()
        catcher.__enter__()
        self._catchers.append(catcher)
        for args, kwargs in self._warning_specs:
            filterwarnings(*args, **kwargs)
        return self

    def __exit__(self, *exc_info):
        catcher = self._catchers.pop()
        return catcher.__exit__(*exc_info)


def ignore_nanwarnings():
    """
    Helper for building a WarningContext that ignores warnings from numpy's
    nanfunctions.
    """
    return WarningContext(
        (
            ('ignore',),
            {'category': RuntimeWarning, 'module': 'numpy.lib.nanfunctions'},
        )
    )


@contextmanager
def ignore_pandas_nan_categorical_warning():
    with catch_warnings():
        # Pandas >= 0.18 doesn't like null-ish values in categories, but
        # avoiding that requires a broader change to how missing values are
        # handled in pipe, so for now just silence the warning.
        filterwarnings(
            'ignore',
            category=FutureWarning,
        )
        yield


def get_algo_instance():
    return getattr(context, 'algorithm', None)

def set_algo_instance(algo):
    context.algorithm = algo


class AlgoAPI(object):
    """
        TLS: ThreadLocalStorage to avoid var confliction / withdraw: tls cannot be gc until thread exit
    """
    def __init__(self, algo_instance):
        self.algo_instance = algo_instance

    def __enter__(self):
        """
        Set the given algo instance, storing any previously-existing instance.
        """
        self.old_algo_instance = get_algo_instance()
        set_algo_instance(self.algo_instance)

    def __exit__(self, _type, _value, _tb):
        """
        Restore the algo instance stored in __enter__.
        """
        set_algo_instance(self.old_algo_instance)


def api_method(f):
    # Decorator that adds the decorated class method as a callable
    # function (wrapped) to zipline.api
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
    # Add functor to zipline.api
    # setattr(zipline.api, f.__name__, wrapped)
    # zipline.api.__all__.append(f.__name__)
    # f.is_api_method = True
    return wrapped

# with ExitStack() as stack:
#     """
#     由于已注册的回调是按注册的相反顺序调用的, 因此最终行为就好像with 已将多个嵌套语句与已注册的一组回调一起使用。
#     这甚至扩展到异常处理-如果内部回调抑制或替换异常，则外部回调将基于该更新状态传递自变量。
#     enter_context  输入一个新的上下文管理器, 并将其__exit__()方法添加到回调堆栈中。返回值是上下文管理器自己的__enter__()方法的结果。
#     callback(回调, * args, ** kwds)接受任意的回调函数和参数, 并将其添加到回调堆栈中。
#     """

