# Copyright (c) 2018 Uber Technologies, Inc.
#
# Licensed under the Uber Non-Commercial License (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at the root directory of this project.
#
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import print_function

import os
from setuptools import setup, Extension, find_packages
from setuptools.command.build_ext import build_ext
from distutils.errors import CompileError, DistutilsPlatformError, LinkError
import sys
import textwrap
import traceback

from jpeg2dct import __version__

common_lib = Extension('jpeg2dct.common.common_lib', [])
numpy_lib = Extension('jpeg2dct.numpy._dctfromjpg_wrapper', [])
tf_lib = Extension('jpeg2dct.tensorflow.tf_lib', [])


def check_tf_version():
    try:
        import tensorflow as tf
        if tf.__version__ < '1.1.0':
            raise DistutilsPlatformError(
                'Your TensorFlow version %s is outdated.  '
                'Horovod requires tensorflow>=1.1.0' % tf.__version__)
    except ImportError:
        raise DistutilsPlatformError(
            'import tensorflow failed, is it installed?\n\n%s' % traceback.format_exc())
    except AttributeError:
        # This means that tf.__version__ was not exposed, which makes it *REALLY* old.
        raise DistutilsPlatformError(
            'Your TensorFlow version is outdated.  Horovod requires tensorflow>=1.1.0')


def get_cpp_flags(build_ext):
    last_err = None
    default_flags = ['-std=c++11', '-fPIC', '-O2']
    if sys.platform == 'darwin':
        # Darwin most likely will have Clang, which has libc++.
        flags_to_try = [default_flags + ['-stdlib=libc++'], default_flags]
    else:
        flags_to_try = [default_flags, default_flags + ['-stdlib=libc++']]
    for cpp_flags in flags_to_try:
        try:
            test_compile(build_ext, 'test_cpp_flags', extra_preargs=cpp_flags,
                         code=textwrap.dedent('''\
                    #include <unordered_map>
                    void test() {
                    }
                    '''))

            return cpp_flags
        except (CompileError, LinkError):
            last_err = 'Unable to determine C++ compilation flags (see error above).'
        except Exception:
            last_err = 'Unable to determine C++ compilation flags.  ' \
                       'Last error:\n\n%s' % traceback.format_exc()

    raise DistutilsPlatformError(last_err)


def get_tf_include_dirs():
    import tensorflow as tf
    tf_inc = tf.sysconfig.get_include()
    return [tf_inc, '%s/external/nsync/public' % tf_inc]


def get_tf_lib_dirs():
    import tensorflow as tf
    tf_lib = tf.sysconfig.get_lib()
    return [tf_lib]


def get_tf_libs(build_ext, lib_dirs, cpp_flags):
    last_err = None
    for tf_libs in [['tensorflow_framework'], []]:
        try:
            lib_file = test_compile(build_ext, 'test_tensorflow_libs',
                                    library_dirs=lib_dirs, libraries=tf_libs,
                                    extra_preargs=cpp_flags,
                                    code=textwrap.dedent('''\
                    void test() {
                    }
                    '''))

            from tensorflow.python.framework import load_library
            load_library.load_op_library(lib_file)

            return tf_libs
        except (CompileError, LinkError):
            last_err = 'Unable to determine -l link flags to use with TensorFlow (see error above).'
        except Exception:
            last_err = 'Unable to determine -l link flags to use with TensorFlow.  ' \
                       'Last error:\n\n%s' % traceback.format_exc()

    raise DistutilsPlatformError(last_err)


def get_tf_abi(build_ext, include_dirs, lib_dirs, libs, cpp_flags):
    last_err = None
    cxx11_abi_macro = '_GLIBCXX_USE_CXX11_ABI'
    for cxx11_abi in ['0', '1']:
        try:
            lib_file = test_compile(build_ext, 'test_tensorflow_abi',
                                    macros=[(cxx11_abi_macro, cxx11_abi)],
                                    include_dirs=include_dirs, library_dirs=lib_dirs,
                                    libraries=libs, extra_preargs=cpp_flags,
                                    code=textwrap.dedent('''\
                #include <string>
                #include "tensorflow/core/framework/op.h"
                #include "tensorflow/core/framework/op_kernel.h"
                #include "tensorflow/core/framework/shape_inference.h"
                void test() {
                    auto ignore = tensorflow::strings::StrCat("a", "b");
                }
                '''))

            from tensorflow.python.framework import load_library
            load_library.load_op_library(lib_file)

            return cxx11_abi_macro, cxx11_abi
        except (CompileError, LinkError):
            last_err = 'Unable to determine CXX11 ABI to use with TensorFlow (see error above).'
        except Exception:
            last_err = 'Unable to determine CXX11 ABI to use with TensorFlow.  ' \
                       'Last error:\n\n%s' % traceback.format_exc()

    raise DistutilsPlatformError(last_err)


def get_tf_flags(build_ext, cpp_flags):
    import tensorflow as tf
    try:
        return tf.sysconfig.get_compile_flags(), tf.sysconfig.get_link_flags()
    except AttributeError:
        # fallback to the previous logic
        tf_include_dirs = get_tf_include_dirs()
        tf_lib_dirs = get_tf_lib_dirs()
        tf_libs = get_tf_libs(build_ext, tf_lib_dirs, cpp_flags)
        tf_abi = get_tf_abi(build_ext, tf_include_dirs,
                            tf_lib_dirs, tf_libs, cpp_flags)

        compile_flags = []
        for include_dir in tf_include_dirs:
            compile_flags.append('-I%s' % include_dir)
        if tf_abi:
            compile_flags.append('-D%s=%s' % tf_abi)

        link_flags = []
        for lib_dir in tf_lib_dirs:
            link_flags.append('-L%s' % lib_dir)
        for lib in tf_libs:
            link_flags.append('-l%s' % lib)

        return compile_flags, link_flags


def test_compile(build_ext, name, code, libraries=None, include_dirs=None, library_dirs=None, macros=None,
                 extra_preargs=None):
    test_compile_dir = os.path.join(build_ext.build_temp, 'test_compile')
    if not os.path.exists(test_compile_dir):
        os.makedirs(test_compile_dir)

    source_file = os.path.join(test_compile_dir, '%s.cc' % name)
    with open(source_file, 'w') as f:
        f.write(code)

    compiler = build_ext.compiler
    [object_file] = compiler.object_filenames([source_file])
    shared_object_file = compiler.shared_object_filename(
        name, output_dir=test_compile_dir)

    compiler.compile([source_file], extra_preargs=extra_preargs,
                     include_dirs=include_dirs, macros=macros)
    compiler.link_shared_object(
        [object_file], shared_object_file, libraries=libraries, library_dirs=library_dirs)

    return shared_object_file


def get_conda_include_dir():
    prefix = os.environ.get('CONDA_PREFIX', '.')
    return [os.path.join(prefix,'include')]


def get_common_options(build_ext):
    cpp_flags = get_cpp_flags(build_ext)

    MACROS = []
    INCLUDES = [] + get_conda_include_dir()
    SOURCES = []
    COMPILE_FLAGS = cpp_flags
    LINK_FLAGS = []
    LIBRARY_DIRS = []
    LIBRARIES = []

    return dict(MACROS=MACROS,
                INCLUDES=INCLUDES,
                SOURCES=SOURCES,
                COMPILE_FLAGS=COMPILE_FLAGS,
                LINK_FLAGS=LINK_FLAGS,
                LIBRARY_DIRS=LIBRARY_DIRS,
                LIBRARIES=LIBRARIES)


def build_common_extension(build_ext, options, abi_compile_flags):
    common_lib.define_macros = options['MACROS']
    common_lib.include_dirs = options['INCLUDES']
    common_lib.sources = options['SOURCES'] + ['jpeg2dct/common/dctfromjpg.cc']
    common_lib.extra_compile_args = options['COMPILE_FLAGS'] + \
                                   abi_compile_flags
    common_lib.extra_link_args = options['LINK_FLAGS']
    common_lib.library_dirs = options['LIBRARY_DIRS']
    common_lib.libraries = options['LIBRARIES'] + ['jpeg']

    build_ext.build_extension(common_lib)


def build_numpy_extension(build_ext, options, abi_compile_flags):
    import numpy
    numpy_lib.define_macros = options['MACROS']
    numpy_lib.include_dirs = options['INCLUDES'] + [numpy.get_include()]
    numpy_lib.sources = options['SOURCES'] + ['jpeg2dct/numpy/dctfromjpg_wrap.cc']
    numpy_lib.extra_compile_args = options['COMPILE_FLAGS'] + \
                                   abi_compile_flags
    numpy_lib.extra_link_args = options['LINK_FLAGS']
    numpy_lib.library_dirs = options['LIBRARY_DIRS']
    numpy_lib.libraries = options['LIBRARIES']

    build_ext.build_extension(numpy_lib)


def build_tf_extension(build_ext, options):
    check_tf_version()
    tf_compile_flags, tf_link_flags = get_tf_flags(
        build_ext, options['COMPILE_FLAGS'])

    tf_lib.define_macros = options['MACROS']
    tf_lib.include_dirs = options['INCLUDES']
    tf_lib.sources = options['SOURCES'] + ['jpeg2dct/tensorflow/tf_lib.cc']
    tf_lib.extra_compile_args = options['COMPILE_FLAGS'] + \
        tf_compile_flags
    tf_lib.extra_link_args = options['LINK_FLAGS'] + tf_link_flags
    tf_lib.library_dirs = options['LIBRARY_DIRS']
    tf_lib.libraries = options['LIBRARIES']

    build_ext.build_extension(tf_lib)

    # Return ABI flags used for TensorFlow compilation.  We will use this flag
    # to compile all the libraries.
    return [flag for flag in tf_compile_flags if '_GLIBCXX_USE_CXX11_ABI' in flag]


# run the customize_compiler
class custom_build_ext(build_ext):
    def build_extensions(self):
        options = get_common_options(self)
        abi_compile_flags = []
        built_plugins = []
        if not os.environ.get('JPEG2DCT_WITHOUT_TENSORFLOW'):
            try:
                abi_compile_flags = build_tf_extension(self, options)
                built_plugins.append(True)
            except:
                if not os.environ.get('JPEG2DCT_WITH_TENSORFLOW'):
                    print('INFO: Unable to build TensorFlow plugin, will skip it.\n\n'
                          '%s' % traceback.format_exc(), file=sys.stderr)
                    built_plugins.append(False)
                else:
                    raise
        build_common_extension(self, options, abi_compile_flags)
        build_numpy_extension(self, options, abi_compile_flags)


setup(name='jpeg2dct',
      version=__version__,
      packages=find_packages(),
      description=textwrap.dedent('''\
          Library providing a Python function and a TensorFlow Op to read JPEG image as a numpy 
          array or a Tensor containing DCT coefficients.'''),
      author='Uber Technologies, Inc.',
      long_description=textwrap.dedent('''\
          jpeg2dct library provides native Python function and a TensorFlow Op to read JPEG image
          as a numpy array or a Tensor containing DCT coefficients.'''),
      url='https://github.com/uber-research/jpeg2dct',
      ext_modules=[common_lib, numpy_lib, tf_lib],
      cmdclass={'build_ext': custom_build_ext},
      setup_requires=['numpy'],
      install_requires=['numpy'],
      tests_require=['pytest'],
      zip_safe=False)
