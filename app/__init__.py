from ._openmp import fix_openmp

fix_openmp()   # must run before numpy / torch are imported anywhere in the app
