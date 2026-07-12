import os
import glob

# sources = glob.glob("**/*.pyx", recursive=True) # **/*.pyx 会搜索当前目录及其所有子目录下的 .pyx 文件
current_dir = os.path.abspath(os.getcwd())


def get_ext_modules(): # trigger by poetry
    import numpy as np
    from setuptools import Extension
    from Cython.Build import cythonize

    # sources = glob.glob("**/*.pyx", recursive=True) # **/*.pyx 会搜索当前目录及其所有子目录下的 .pyx 文件
    current_dir = os.path.abspath(os.getcwd())

    extensions = [ 
        Extension(
            name="bt_core.timer", 
            sources=["bt_core/timer.pyx"],
            include_dirs=[np.get_include(), current_dir],
            language="c++", # vector/map
            extra_compile_args=["-O3", "-std=c++11"],
            # "-Wno-unused-function",
            # "-Wno-unused-variable",
            # "-Wno-unused-but-set-variable",
            # "-Wno-unused-parameter",
            # "-Wno-sign-compare" 
        ),
        Extension(
            name="bt_core.pnc", 
            sources=["bt_core/pnc.pyx"],
            include_dirs=[np.get_include(), current_dir],
            language="c++", # vector/map
            extra_compile_args=["-O3", "-std=c++11"],
        ),
        Extension(
            name="bt_core.sizer", 
            sources=["bt_core/sizer.pyx"],
            include_dirs=[np.get_include(), current_dir],
            language="c++", # vector/map
            extra_compile_args=["-O3", "-std=c++11"],
        ),
        Extension(
            name="bt_core.shm.shm_buffer", 
            sources=["bt_core/shm/shm_buffer.pyx"],
            include_dirs=[np.get_include(), current_dir],
            language="c++", # vector/map
            extra_compile_args=["-O3", "-std=c++11"],
        ),
        Extension(
            name="bt_core.sink.sink", 
            sources=["bt_core/sink/sink.pyx"],
            include_dirs=[np.get_include(), current_dir],
            language="c++", # vector/map
            extra_compile_args=["-O3", "-std=c++11"],
        ),
        Extension(
            name="bt_core.utils.dateintern", 
            sources=["bt_core/utils/dateintern.pyx"],
            include_dirs=[np.get_include(), current_dir],
            language="c++", # vector/map
            extra_compile_args=["-O3", "-std=c++11"],
            ),
        Extension(
            name="bt_core.utils.util", 
            sources=["bt_core/utils/util.pyx"],
            include_dirs=[np.get_include(), current_dir], # '.'
            language="c++", # vector/map
            extra_compile_args=["-O3", "-std=c++11"],
        ),
        Extension(
            name="bt_core.execution.actor.writer_actor", 
            sources=["bt_core/execution/actor/writer_actor.pyx"],
            include_dirs=[np.get_include(), "."],  
            language="c++",                         # vector/map
            extra_compile_args=["-O3", "-std=c++11"]
        ),
        Extension(
            name="bt_core.execution.gateway.interface", 
            sources=["bt_core/execution/gateway/interface.pyx"],
            include_dirs=[np.get_include(), current_dir, "."], # . root 
            language="c++",
            extra_compile_args=["-O3", "-std=c++11"]
        ),
        Extension(
            name="bt_core.execution.core.engine.engine", 
            sources=["bt_core/execution/core/engine/engine.pyx"],
            include_dirs=[np.get_include(), current_dir, "."],
            language="c++",
        ),
        Extension(
            name="bt_core.execution.trade_api", 
            sources=["bt_core/execution/trade_api.pyx"],
            include_dirs=[np.get_include(), current_dir, "."],
            language="c++",
        )]

    # finance 
    finance_sources = glob.glob("bt_core/execution/core/finance/*.pyx")

    for src in finance_sources:
        # "core/finance/account.pyx" -> "core.finance.account"
        module_name = src.replace("/", ".").replace(".pyx", "")

        extensions.append(
            Extension(
                name=module_name,
                sources=[src],
                include_dirs=[np.get_include(), current_dir],
                language="c++",
                extra_compile_args=["-O3", "-std=c++11"]
            )
        )

    compiler_directives={
        'language_level': "3",       # 使用 Python 3 语法
        'boundscheck': False,        # 关闭数组越界检查（提升性能）
        'wraparound': False,         # 关闭负索引支持（提升性能）
        'initializedcheck': False,   # 关闭内存视图初始化检查
        'cdivision': True,           # 开启 C 级别除法（不检查除零，极快）
    }


    ext_modules=cythonize(
        extensions,
        compiler_directives=compiler_directives,
        annotate=False # .html 文件，方便查看代码是否实现C 级加速
    )
    return ext_modules


if __name__ == "__main__":
    from setuptools import setup, find_packages

    setup(
        name="bt_core",
        packages=find_packages(),
        include_package_data=True, 
        ext_modules=get_ext_modules(),
    )
