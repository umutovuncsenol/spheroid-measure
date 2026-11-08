"""Avoid the 'OMP: Error #15 ... libomp already initialized' crash.

PyTorch's pip wheel ships its own OpenMP runtime (torch/lib/libomp.dylib on macOS,
torch/lib/libiomp5md.dll on Windows), while a conda environment usually has a second one
(llvm-openmp / intel-openmp). When both get loaded into one process the app aborts the moment
Cellpose starts ("Abort trap: 6" on macOS) - before any detection runs.

This module runs before numpy or torch are imported (app/__init__.py imports it first) and
leaves exactly one runtime:

* macOS: torch's private copy is replaced by a symlink to the environment's libomp.dylib
  (the original is kept next to it as libomp.dylib.bak). This is done once per installation
  and repeated automatically if torch is reinstalled. KMP_DUPLICATE_LIB_OK is NOT used on macOS:
  with two runtimes loaded it still segfaults later (seen in practice).
* Windows: symlinks need admin rights, so the documented fallback KMP_DUPLICATE_LIB_OK=TRUE is
  set, only when both copies are actually present.

Nothing happens when only one runtime exists (e.g. a plain .venv install).
"""
import importlib.util
import os
import sys


def _torch_lib_dir():
    try:
        spec = importlib.util.find_spec('torch')     # locates the package without importing it
    except (ImportError, ValueError):
        return None
    if not spec or not spec.origin:
        return None
    return os.path.join(os.path.dirname(spec.origin), 'lib')


def _same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def fix_openmp():
    lib = _torch_lib_dir()
    if not lib:
        return
    if sys.platform == 'darwin':
        env_omp = os.path.join(sys.prefix, 'lib', 'libomp.dylib')
        torch_omp = os.path.join(lib, 'libomp.dylib')
        if not os.path.isfile(env_omp) or not os.path.lexists(torch_omp) or _same_file(env_omp, torch_omp):
            return
        try:
            if os.path.islink(torch_omp):            # a stale or wrong link: replace it
                os.remove(torch_omp)
            else:
                os.replace(torch_omp, torch_omp + '.bak')
            os.symlink(env_omp, torch_omp)
            print(f'[setup] Linked PyTorch to the environment OpenMP runtime ({env_omp}) to avoid a crash.', flush=True)
        except OSError as e:
            print(f'[setup] WARNING: could not fix the duplicate OpenMP runtime ({e}). Detection may crash with '
                  f'"OMP: Error #15". Fix by hand:  mv "{torch_omp}" "{torch_omp}.bak" && ln -s "{env_omp}" "{torch_omp}"',
                  flush=True)
    elif sys.platform == 'win32':
        env_omp = os.path.join(sys.prefix, 'Library', 'bin', 'libiomp5md.dll')
        torch_omp = os.path.join(lib, 'libiomp5md.dll')
        if os.path.isfile(env_omp) and os.path.isfile(torch_omp):
            os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
