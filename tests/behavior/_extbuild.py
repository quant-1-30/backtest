import glob
import importlib.util
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
MODULE = "_finance_behavior"

# 保证 cimport 路径与运行时 import 都能找到包
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _so_path():
    candidates = sorted(glob.glob(str(HERE / f"{MODULE}*.so")))
    return Path(candidates[0]) if candidates else None


def _fresh(so: Path) -> bool:
    src = HERE / f"{MODULE}.pyx"
    if src.stat().st_mtime >= so.stat().st_mtime:
        return False
    for pxd in (REPO / "bt_core").rglob("*.pxd"):
        if pxd.stat().st_mtime >= so.stat().st_mtime:
            return False
    return True


def _build():
    setup_code = f'''
from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np

setup(
    name="bt_behavior_tests",
    ext_modules=cythonize(
        [Extension(
            "{MODULE}",
            sources=["{MODULE}.pyx"],
            include_dirs=[np.get_include(), r"{REPO}"],
            language="c++",
            extra_compile_args=["-O0", "-std=c++11"],
        )],
        language_level=3,
    ),
)
'''
    # cwd=HERE: build_ext --inplace 把裸扩展的 .so 放在当前目录
    subprocess.run(
        [sys.executable, "-c", setup_code, "build_ext", "--inplace"],
        cwd=HERE, check=True,
    )


def import_or_build():
    so = _so_path()
    if so is None or not _fresh(so):
        _build()
        so = _so_path()
        if so is None:
            raise RuntimeError(f"{MODULE} build produced no .so in {HERE}")

    spec = importlib.util.spec_from_file_location(MODULE, so)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = mod
    spec.loader.exec_module(mod)
    return mod


if __name__ == "__main__":
    m = import_or_build()
    print("loaded:", m.__file__)
