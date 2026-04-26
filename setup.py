import numpy
from setuptools import setup, Extension
from Cython.Build import cythonize

setup(
    ext_modules=cythonize(
        [
            Extension(
                "cells_helpers",
                ["cells_helpers.pyx"],
                include_dirs=[numpy.get_include()],
                define_macros=[("NPY_NO_DEPRECATED_API", "NPY_1_7_API_VERSION")],
            )
        ]
    ),
)
