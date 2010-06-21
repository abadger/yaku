import sys
import os
import copy
import distutils
import re

from subprocess \
    import \
        Popen, PIPE, STDOUT

from yaku.task_manager \
    import \
        create_tasks, topo_sort, build_dag, \
        CompiledTaskGen, set_extension_hook
from yaku.sysconfig \
    import \
        get_configuration, detect_distutils_cc
from yaku.compiled_fun \
    import \
        compile_fun
from yaku.task \
    import \
        Task
from yaku.utils \
    import \
        ensure_dir
from yaku.conftests \
    import \
        check_compiler, check_header

import yaku.tools

pylink, pylink_vars = compile_fun("pylink", "${PYEXT_SHLINK} ${PYEXT_SHLINKFLAGS} ${PYEXT_APP_LIBDIR} ${PYEXT_APP_LIBS} ${PYEXT_LINK_TGT_F}${TGT[0]} ${PYEXT_LINK_SRC_F}${SRC}", False)

pycc, pycc_vars = compile_fun("pycc", "${PYEXT_SHCC} ${PYEXT_CCSHARED} ${PYEXT_CFLAGS} ${PYEXT_INCPATH} ${PYEXT_CC_TGT_F}${TGT[0]} ${PYEXT_CC_SRC_F}${SRC}", False)

# pyext env <-> sysconfig env conversion
_SYS_TO_PYENV = {
        "PYEXT_SHCC": "CC",
        "PYEXT_CCSHARED": "CCSHARED",
        "PYEXT_SHLINK": "LDSHARED",
        "PYEXT_SUFFIX": "SO",
        "PYEXT_CFLAGS": "CFLAGS",
        "PYEXT_OPT": "OPT",
}

_PYENV_REQUIRED = [
        "LIBDIR",
        "LIBDIR_FMT",
        "LIBS",
        "LIB_FMT",
        "CPPPATH_FMT",
        "CC_TGT_F",
        "CC_SRC_F",
        "LINK_TGT_F",
        "LINK_SRC_F",
]

_SYS_TO_CCENV = {
        "CC": "CC",
        "SHCC": "CCSHARED",
        "SHLINK": "LDSHARED",
        "SO": "SO",
        "CFLAGS": "CFLAGS",
        "OPT": "OPT",
        "LIBDIR": "LIBDIR",
        "LIBDIR_FMT": "LIBDIR_FMT",
        "LIBS": "LIBS",
        "LIB_FMT": "LIB_FMT",
        "CPPPATH_FMT": "CPPPATH_FMT",
        "CC_TGT_F": "CC_TGT_F",
        "CC_SRC_F": "CC_SRC_F",
}

def get_pyenv(ctx):
    pyenv = {}
    sysenv = get_configuration()
    for i, j in _SYS_TO_PYENV.items():
        pyenv[i] = sysenv[j]

    for k in _PYENV_REQUIRED:
        pyenv["PYEXT_%s" % k] = ctx.env[k]
    ## FIXME: how to deal with CC* namespace ?
    #for i, j in _SYS_TO_CCENV.items():
    #    pyenv[i] = sysenv[j]
    pyenv["PYEXT_CPPPATH"] = [distutils.sysconfig.get_python_inc()]
    pyenv["PYEXT_SHLINKFLAGS"] = []
    return pyenv

def pycc_hook(self, node):
    tasks = pycc_task(self, node)
    self.object_tasks.extend(tasks)
    return tasks

def pycc_task(self, node):
    base = os.path.splitext(node)[0]
    # XXX: hack to avoid creating build/build/... when source is
    # generated. Dealing with this most likely requires a node concept
    if not os.path.commonprefix([self.env["BLDDIR"], base]):
        target = os.path.join(self.env["BLDDIR"], base + ".o")
    else:
        target = base + ".o"
    ensure_dir(target)
    task = Task("pycc", inputs=node, outputs=target)
    task.env_vars = pycc_vars
    task.env = self.env
    task.func = pycc
    return [task]

def pylink_task(self, name):
    objects = [tsk.outputs[0] for tsk in self.object_tasks]
    target = os.path.join(self.env["BLDDIR"], name + ".so")
    ensure_dir(target)
    task = Task("pylink", inputs=objects, outputs=target)
    task.func = pylink
    task.env_vars = pylink_vars
    return [task]

class PythonBuilder(object):
    def __init__(self, ctx):
        self.ctx = ctx
        self.env = copy.deepcopy(ctx.env)

    def extension(self, name, sources, env=None):
        _env = copy.deepcopy(self.env)
        if env is not None:
            _env.update(env)
        return create_pyext(self.ctx, _env, name, sources)

def get_builder(ctx):
    return PythonBuilder(ctx)

CC_SIGNATURE = {
        "gcc": re.compile("gcc version"),
        "msvc": re.compile("Microsoft \(R\) 32-bit C/C\+\+ Optimizing Compiler")
}

def detect_cc_type(ctx, cc_cmd):
    cc_type = None

    def detect_type(vflag):
        cmd = cc_cmd + [vflag]
        try:
            p = Popen(cmd, stdout=PIPE, stderr=STDOUT)
            out = p.communicate()[0]
            for k, v in CC_SIGNATURE.items():
                m = v.search(out)
                if m:
                    return k
        except OSError:
            pass
        return None

    sys.stderr.write("Detecting CC type... ")
    if sys.platform == "win32":
        for v in [""]:
            cc_type = detect_type(v)
    else:
        for v in ["-v", "-V", "-###"]:
            cc_type = detect_type(v)
            if cc_type:
                break
        if cc_type is None:
            cc_type = "cc"
    sys.stderr.write("%s\n" % cc_type)
    return cc_type

def configure(ctx):
    # What to do here:
    # - get default compiler for host
    # - check that it is found
    # - check that it works

    cc = detect_distutils_cc(ctx)
    cc_type = detect_cc_type(ctx, cc)
    sys.path.insert(0, os.path.dirname(yaku.tools.__file__))
    try:
        mod = __import__(cc_type)
        mod.detect(ctx)
    finally:
        sys.path.pop(0)

    ctx.env.update(get_pyenv(ctx))

def create_pyext(bld, env, name, sources):
    base = name.replace(".", os.sep)

    tasks = []

    task_gen = CompiledTaskGen("pyext", sources, name)
    old_hook = set_extension_hook(".c", pycc_hook)

    task_gen.env = env
    apply_cpppath(task_gen)
    apply_libpath(task_gen)
    apply_libs(task_gen)

    tasks = create_tasks(task_gen, sources)

    ltask = pylink_task(task_gen, base)
    tasks.extend(ltask)
    for t in tasks:
        t.env = task_gen.env

    set_extension_hook(".c", old_hook)
    bld.tasks.extend(tasks)

    outputs = []
    for t in ltask:
        outputs.extend(t.outputs)
    return outputs

# FIXME: find a way to reuse this kind of code between tools
def apply_libs(task_gen):
    libs = task_gen.env["LIBS"]
    task_gen.env["PYEXT_APP_LIBS"] = [
            task_gen.env["LIBS_FMT"] % lib for lib in libs]

def apply_libpath(task_gen):
    libdir = task_gen.env["LIBDIR"]
    implicit_paths = set([
        os.path.join(task_gen.env["BLDDIR"], os.path.dirname(s))
        for s in task_gen.sources])
    libdir = list(implicit_paths) + libdir
    task_gen.env["PYEXT_APP_LIBDIR"] = [
            task_gen.env["LIBDIR_FMT"] % d for d in libdir]

def apply_cpppath(task_gen):
    cpppaths = task_gen.env["CPPPATH"]
    cpppaths.extend(task_gen.env["PYEXT_CPPPATH"])
    implicit_paths = set([
        os.path.join(task_gen.env["BLDDIR"], os.path.dirname(s))
        for s in task_gen.sources])
    cpppaths = list(implicit_paths) + cpppaths
    task_gen.env["PYEXT_INCPATH"] = [
            task_gen.env["PYEXT_CPPPATH_FMT"] % p
            for p in cpppaths]
