from tests.behavior import _extbuild

mod = _extbuild.import_or_build()


# 把 Cython 模块里的 test_* 包成真正的 Python 函数（pytest 不收集
# cython_function_or_method），通过闭包保持引用
def _collect():
    for name in dir(mod):
        if not name.startswith("test_"):
            continue
        fn = getattr(mod, name)
        if not callable(fn):
            continue

        def wrapper(fn=fn):
            fn()

        wrapper.__name__ = name
        yield wrapper


for _w in _collect():
    globals()[_w.__name__] = _w
del _w
