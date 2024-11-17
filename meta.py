# /usr/bin/env python3
# -*- coding: utf-8 -*-
import pickle
import numpy as np
import weakref
import sys
import itertools
import sys
import logging
import copy
import datetime
from numpy.random import default_rng
from textwrap import dedent
from typing import Any, List
from numpy import isfinite


def findbases(kls, topclass):
    retval = list()
    for base in kls.__bases__:
        if issubclass(base, topclass):
            retval.extend(findbases(base, topclass))
            retval.append(base)

    return retval


def findowner(owned, cls, startlevel=2, skip=None):
    # skip this frame and the caller's -> start at 2
    for framelevel in itertools.count(startlevel):
        try:
            frame = sys._getframe(framelevel)
        except ValueError:
            # Frame depth exceeded ... no owner ... break away
            break

        # 'self' in regular code
        self_ = frame.f_locals.get('self', None)
        if skip is not self_:
            if self_ is not owned and isinstance(self_, cls):
                return self_

        # '_obj' in metaclasses
        obj_ = frame.f_locals.get('_obj', None)
        if skip is not obj_:
            if obj_ is not owned and isinstance(obj_, cls):
                return obj_
    return None


# class Param(object):
#     """
#         类实例属性赋值调用__setattr__ --- 负责在__dict__注册; 所以重载__setattr__注意, 要不手动注册__dict__
#     """
#     # __slots__ = ("params")

#     def __init__(self, params):
#         # self.params = dict(params) if isinstance(params, tuple) else params
#         params = dict(params) if isinstance(params, tuple) else params
#         for k, v in params.items():
#             self.__dict__[k] = v

#     def __setattr__(self, __name: str, __value: Any) -> None:
#         raise ValueError("params is immutable")
        

class MetaBase(type):

    def __new__(cls, name, bases, attrs):
        print("Creating MetaBase class bases ", bases)
        print("Creating MetaBase class bases attrs ", attrs)
        
        # if "on_handle" not in attrs:
        #     raise TypeError(f"{name} must implement on_handle method")

        # update params
        params = dict(attrs.pop("params", {}))
        print("original params ", params)
        # baseparam
        # if len(bases) == 1 and bases[0] == object:
        #     morebasesparams = []
        # else:
        #     morebasesparams = [copy.copy(x.p.__dict__) for x in bases[1:] if hasattr(x, 'params') if hasattr(x, "p")]
        morebasesparams = [copy.copy(x.p.__dict__) for x in bases if hasattr(x, 'p')]
        # update param
        print("morebasesparams ", morebasesparams)
        for d in morebasesparams:
            params.update(d)
        print("update params", params)

        # clsmodule = sys.modules[cls.__module__]
        # cls.__name__ 为 MetaBase ; name为子类
        if "alias" not in params:
            newclsname = str(cls.__name__ + '_' + name)  # str - Python 2/3 compat
        else:
            newclsname = params.pop("alias")
        # type 与 type.__new__ 区别前者生产新类, 后者实例自动触发init
        # newcls = type(newclsname, bases, attrs)
        newcls = type.__new__(cls, newclsname, bases, attrs)
        # set param
        p = Param(params=params)
        setattr(newcls, "p", p)
        return newcls
    
    def __init__(cls, name, bases, dct):
        # postprocess
        cls.doinit()
        print("entering into Metabase __init__")

    def doinit(cls):
        # print("entering into doinit")
        pass 

    # # python 对象创建两种方式 Python/C Api ; 调用对象. 如果实例对象需要定义__call__, 如果
    # def __call__(cls, *args: Any, **kwds: Any) -> Any:
    #     print("entering into metabase __call__", args, kwds)
    #     return super().__call__(*args, **kwds)


# This is from Armin Ronacher from Flash simplified later by six
def with_metaclass(meta, *bases):
    """Create a base class with a metaclass."""
    # This requires a bit of explanation: the basic idea is to make a dummy
    # metaclass for one level of class instantiation that replaces itself with
    # the actual metaclass.
    class metaclass(meta):

        def __new__(cls, name, this_bases, d):
            # print("------d", d)
            # print("this_bases", this_bases, type(this_bases[0]), len(this_bases))
            print("entering into with_metaclass")
            # stub to allow easy subclassing without metaclasses
            m = meta(name, bases, d)
            # type.__new__ 自动触发__init__
            # super().__init__(m, name, bases, d)
            return m
    return type.__new__(metaclass, "temporary_class", (), {})


class ParamBase(with_metaclass(MetaBase, object)):
    pass


class SingletonMeta(type):

    _instances = {}

    def __call__(cls, *args: Any, **kwds: Any) -> Any:
        if cls not in cls._instances:
            instance = super().__call__(*args, **kwds)
            cls._instances[cls] = instance
        return cls._instances[cls]


class MetaLogger(type):
    def __new__(mcs, name, bases, attrs):  # pylint: disable=C0204
        wrapper_dict = logging.Logger.__dict__.copy()
        for key, val in wrapper_dict.items():
            if key not in attrs and key != "__reduce__":
                attrs[key] = val
        return type.__new__(mcs, name, bases, attrs)
    