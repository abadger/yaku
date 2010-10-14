import sys
import os
import copy

import yaku.tools

from yaku.task \
    import \
        task_factory
from yaku.task_manager \
    import \
        extension, CompiledTaskGen
from yaku.utils \
    import \
        find_deps, ensure_dir
from yaku.compiled_fun \
    import \
        compile_fun

ccompile, cc_vars = compile_fun("cc", "${CC} ${CFLAGS} ${INCPATH} ${CC_TGT_F}${TGT[0].abspath()} ${CC_SRC_F}${SRC}", False)

ccprogram, ccprogram_vars = compile_fun("ccprogram", "${LINK} ${LINK_TGT_F}${TGT[0].abspath()} ${LINK_SRC_F}${SRC} ${APP_LIBDIR} ${APP_LIBS} ${LINKFLAGS}", False)

cshlink, cshlink_vars = compile_fun("cshlib", "${SHLINK} ${APP_LIBDIR} ${APP_LIBS} ${SHLINK_TGT_F}${TGT[0]} ${SHLINK_SRC_F}${SRC} ${SHLINKFLAGS}", False)

clink, clink_vars = compile_fun("clib", "${STLINK} ${STLINKFLAGS} ${STLINK_TGT_F}${TGT[0].abspath()} ${STLINK_SRC_F}${SRC}", False)

@extension('.c')
def c_hook(self, node):
    tasks = ccompile_task(self, node)
    self.object_tasks.extend(tasks)
    return tasks

def ccompile_task(self, node):
    base = self.env["CC_OBJECT_FMT"] % node.name
    target = node.parent.declare(base)
    ensure_dir(target.abspath())

    task = task_factory("cc")(inputs=[node], outputs=[target])
    task.gen = self
    task.env_vars = cc_vars
    #print find_deps("foo.c", ["."])
    #task.scan = lambda : find_deps(node, ["."])
    #task.deps.extend(task.scan())
    task.env = self.env
    task.func = ccompile
    return [task]

def shlink_task(self, name):
    objects = [tsk.outputs[0] for tsk in self.object_tasks]
    target = os.path.join(self.env["BLDDIR"], name + ".so")
    ensure_dir(target)

    task = task_factory("cc_shlink")(inputs=objects, outputs=target)
    task.gen = self
    task.env = self.env
    task.func = cshlink
    task.env_vars = cshlink_vars
    return [task]

def static_link_task(self, name):
    objects = [tsk.outputs[0] for tsk in self.object_tasks]

    folder, base = os.path.split(name)
    tmp = folder + os.path.sep + self.env["STATICLIB_FMT"] % base
    target = self.bld.bld_root.declare(tmp)
    ensure_dir(target.abspath())

    task = task_factory("cc_link")(inputs=objects, outputs=[target])
    task.gen = self
    task.env = self.env
    task.func = clink
    task.env_vars = clink_vars
    return [task]

def ccprogram_task(self, name):
    objects = [tsk.outputs[0] for tsk in self.object_tasks]
    def declare_target():
        folder, base = os.path.split(name)
        tmp = folder + os.path.sep + self.env["PROGRAM_FMT"] % base
        return self.bld.bld_root.declare(tmp)
    target = declare_target()
    ensure_dir(target.abspath())

    task = task_factory("ccprogram")(inputs=objects, outputs=[target])
    task.gen = self
    task.env = self.env
    task.func = ccprogram
    task.env_vars = ccprogram_vars
    return [task]

def apply_cpppath(task_gen):
    cpppaths = task_gen.env["CPPPATH"]
    implicit_paths = set([s.parent.srcpath() \
                          for s in task_gen.sources])
    srcnode = task_gen.sources[0].ctx.srcnode

    relcpppaths = []
    for p in cpppaths:
        if not os.path.isabs(p):
            node = srcnode.find_node(p)
            assert node is not None, "could not find %s" % p
            relcpppaths.append(node.bldpath())
        else:
            relcpppaths.append(p)
    cpppaths = list(implicit_paths) + relcpppaths
    task_gen.env["INCPATH"] = [
            task_gen.env["CPPPATH_FMT"] % p
            for p in cpppaths]
def apply_libs(task_gen):
    libs = task_gen.env["LIBS"]
    task_gen.env["APP_LIBS"] = [
            task_gen.env["LIB_FMT"] % lib for lib in libs]

def apply_libdir(task_gen):
    libdir = task_gen.env["LIBDIR"]
    task_gen.env["APP_LIBDIR"] = [
            task_gen.env["LIBDIR_FMT"] % d for d in libdir]

def _merge_env(_env, new_env):
    if new_env is not None:
        ret = copy.copy(_env)
        for k, v in new_env.items():
            if hasattr(_env[k], "extend"):
                old = copy.copy(_env[k])
                old.extend(v)
                ret[k] = old
            else:
                ret[k] = v
        return ret
    else:
        return _env

class CCBuilder(object):
    def clone(self):
        return CCBuilder(self.ctx)

    def __init__(self, ctx):
        self.ctx = ctx
        self.env = copy.deepcopy(ctx.env)

    def ccompile(self, name, sources, env=None):
        task_gen = CompiledTaskGen("cccompile", self.ctx,
                                   sources, name)
        task_gen.env = _merge_env(self.env, env)
        apply_cpppath(task_gen)

        tasks = task_gen.process()
        for t in tasks:
            t.env = task_gen.env
        self.ctx.tasks.extend(tasks)

        outputs = []
        for t in tasks:
            outputs.extend(t.outputs)
        return outputs

    def static_library(self, name, sources, env=None):
        sources = [self.ctx.src_root.find_resource(s) for s in sources]
        task_gen = CompiledTaskGen("ccstaticlib", self.ctx, sources, name)
        task_gen.env = _merge_env(self.env, env)
        apply_cpppath(task_gen)
        apply_libdir(task_gen)
        apply_libs(task_gen)

        tasks = task_gen.process()
        ltask = static_link_task(task_gen, name)
        tasks.extend(ltask)
        for t in tasks:
            t.env = task_gen.env
        self.ctx.tasks.extend(tasks)
        self.link_task = ltask

        outputs = []
        for t in ltask:
            outputs.extend(t.outputs)
        return outputs


    def program(self, name, sources, env=None):
        sources = [self.ctx.src_root.find_resource(s) for s in sources]
        task_gen = CompiledTaskGen("ccprogram", self.ctx,
                                   sources, name)
        task_gen.env = _merge_env(self.env, env)
        apply_cpppath(task_gen)
        apply_libdir(task_gen)
        apply_libs(task_gen)

        tasks = task_gen.process()
        ltask = ccprogram_task(task_gen, name)
        tasks.extend(ltask)
        for t in tasks:
            t.env = task_gen.env
        self.ctx.tasks.extend(tasks)
        self.link_task = ltask

        outputs = []
        for t in ltask:
            outputs.extend(t.outputs)
        return outputs

def configure(ctx):
    if sys.platform == "win32":
        candidates = ["msvc", "gcc"]
    else:
        candidates = ["gcc", "cc"]

    def _detect_cc():
        detected = None
        sys.path.insert(0, os.path.dirname(yaku.tools.__file__))
        try:
            for cc_type in candidates:
                sys.stderr.write("Looking for %s... " % cc_type)
                try:
                    mod = __import__(cc_type)
                    if mod.detect(ctx):
                        sys.stderr.write("yes\n")
                        detected = cc_type
                        break
                except:
                    pass
                sys.stderr.write("no!\n")
            return detected
        finally:
            sys.path.pop(0)

    cc_type = _detect_cc()
    if cc_type is None:
        raise ValueError("No C compiler found!")
    cc = ctx.load_tool(cc_type)
    cc.setup(ctx)

    if sys.platform == "win32":
        raise NotImplementedError("cstatic lib not supported yet")
    else:
        ar = ctx.load_tool("ar")
        ar.setup(ctx)

def get_builder(ctx):
    return CCBuilder(ctx)
