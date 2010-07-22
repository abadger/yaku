import sys
import os
import copy
import distutils
import distutils.sysconfig
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

pylink, pylink_vars = compile_fun("pylink", "${PYEXT_SHLINK} ${PYEXT_LINK_TGT_F}${TGT[0]} ${PYEXT_LINK_SRC_F}${SRC} ${PYEXT_SHLINKFLAGS} ${PYEXT_APP_LIBDIR} ${PYEXT_APP_LIBS}", False)

pycc, pycc_vars = compile_fun("pycc", "${PYEXT_CC} ${PYEXT_CFLAGS} ${PYEXT_INCPATH} ${PYEXT_CC_TGT_F}${TGT[0]} ${PYEXT_CC_SRC_F}${SRC}", False)

# pyext env <-> sysconfig env conversion
_SYS_TO_PYENV = {
        "PYEXT_SHCC": "CC",
        "PYEXT_CCSHARED": "CCSHARED",
        "PYEXT_SHLINK": "LDSHARED",
        "PYEXT_SUFFIX": "SO",
        "PYEXT_CFLAGS": "CFLAGS",
        "PYEXT_OPT": "OPT",
        "PYEXT_LIBDIR": "LIBDIR",
}

_PYENV_REQUIRED = [
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

def setup_pyext_env(ctx, cc_type="default", use_distutils=True):
    pyenv = {}
    if use_distutils:
        if cc_type == "default":
            dist_env = get_configuration()
        else:
            dist_env = get_configuration(cc_type)
    else:
        dist_env = {}
        vars = ["CC", "CPPPATH", "BASE_CFLAGS", "OPT", "SHARED", "SHLINK",
                "LDFLAGS", "LIBDIR", "LIBS"]
        for k in vars:
            dist_env[k] = ctx.env.get(k, [])
        dist_env["CPPPATH"].append(distutils.sysconfig.get_python_inc())
        dist_env["SO"] = distutils.sysconfig.get_config_var("SO")

    for name, value in dist_env.items():
        pyenv["PYEXT_%s" % name] = value
    pyenv["PYEXT_FMT"] = "%%s%s" % dist_env["SO"]
    pyenv["PYEXT_CFLAGS"] = pyenv["PYEXT_BASE_CFLAGS"] + \
            pyenv["PYEXT_OPT"] + \
            pyenv["PYEXT_SHARED"]
    pyenv["PYEXT_SHLINKFLAGS"] = dist_env["LDFLAGS"]
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
        target = os.path.join(self.env["BLDDIR"],
                self.env["PYEXT_CC_OBJECT_FMT"] % base)
    else:
        target = self.env["PYEXT_CC_OBJECT_FMT"] % base

    ensure_dir(target)
    task = Task("pycc", inputs=node, outputs=target)
    task.env_vars = pycc_vars
    task.env = self.env
    task.func = pycc
    return [task]

def pylink_task(self, name):
    objects = [tsk.outputs[0] for tsk in self.object_tasks]
    target = os.path.join(self.env["BLDDIR"],
            self.env["PYEXT_FMT"] % name)
    ensure_dir(target)
    task = Task("pylink", inputs=objects, outputs=target)
    task.func = pylink
    task.env_vars = pylink_vars
    return [task]

class PythonBuilder(object):
    def __init__(self, ctx):
        self.ctx = ctx
        self.env = copy.deepcopy(ctx.env)
        self.compiler_type = "default"
        self.use_distutils = True

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
        for v in ["", "-v"]:
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

def get_distutils_cc_exec(ctx, compiler_type="default"):
    from distutils import ccompiler

    sys.stderr.write("Detecting distutils CC exec ... ")
    if compiler_type == "default":
        compiler_type = \
                distutils.ccompiler.get_default_compiler()

    compiler = ccompiler.new_compiler(compiler=compiler_type)
    if compiler_type == "msvc":
        compiler.initialize()
        cc = [compiler.cc]
    else:
        cc = compiler.compiler_so
    sys.stderr.write("%s\n" % " ".join(cc))
    return cc

def configure(ctx):
    # How we do it
    # 1: for distutils-based configuration
    #   - get compile/flags flags from sysconfig
    #   - detect yaku tool name from CC used by distutils:
    #       - get the compiler executable used by distutils ($CC
    #       variable)
    #       - try to determine yaku tool name from $CC
    #   - apply necessary variables from yaku tool to $PYEXT_
    #   "namespace"
    compiler_type = ctx.builders["pyext"].compiler_type

    if ctx.builders["pyext"].use_distutils:
        dist_env = setup_pyext_env(ctx, compiler_type)
        ctx.env.update(dist_env)

        cc_exec = get_distutils_cc_exec(ctx, compiler_type)
        yaku_cc_type = detect_cc_type(ctx, cc_exec)

        _setup_compiler(ctx, yaku_cc_type)
    else:
        dist_env = setup_pyext_env(ctx, compiler_type, False)
        ctx.env.update(dist_env)

        _setup_compiler(ctx, compiler_type, use_distutils=False)

def _setup_compiler(ctx, cc_type, use_distutils=True):
    old_env = ctx.env
    ctx.env = {}
    cc_env = None
    sys.path.insert(0, os.path.dirname(yaku.tools.__file__))
    try:
        try:
            mod = __import__(cc_type)
            mod.setup(ctx)
        except ImportError:
            raise RuntimeError("No tool %s is available (import failed)" \
                            % cc_type)

        # XXX: this is ugly - find a way to have tool-specific env...
        cc_env = ctx.env
    finally:
        sys.path.pop(0)
        ctx.env = old_env

    copied_values = ["CPPPATH_FMT", "LIBDIR_FMT", "LIB_FMT",
            "CC_OBJECT_FMT", "CC_TGT_F", "CC_SRC_F", "LINK_TGT_F",
            "LINK_SRC_F"]
    if not use_distutils:
        copied_values.extend(["CC", "SHLINK"])
    for k in copied_values:
        ctx.env["PYEXT_%s" % k] = cc_env[k]

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
    libs = task_gen.env["PYEXT_LIBS"]
    task_gen.env["PYEXT_APP_LIBS"] = [
            task_gen.env["PYEXT_LIB_FMT"] % lib for lib in libs]

def apply_libpath(task_gen):
    libdir = task_gen.env["PYEXT_LIBDIR"]
    implicit_paths = set([
        os.path.join(task_gen.env["BLDDIR"], os.path.dirname(s))
        for s in task_gen.sources])
    libdir = list(implicit_paths) + libdir
    task_gen.env["PYEXT_APP_LIBDIR"] = [
            task_gen.env["PYEXT_LIBDIR_FMT"] % d for d in libdir]

def apply_cpppath(task_gen):
    cpppaths = task_gen.env["PYEXT_CPPPATH"]
    implicit_paths = set([
        os.path.join(task_gen.env["BLDDIR"], os.path.dirname(s))
        for s in task_gen.sources])
    cpppaths = list(implicit_paths) + cpppaths
    task_gen.env["PYEXT_INCPATH"] = [
            task_gen.env["PYEXT_CPPPATH_FMT"] % p
            for p in cpppaths]
