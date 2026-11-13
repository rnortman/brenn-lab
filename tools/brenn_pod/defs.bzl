"""fixed_name_executable: an executable at a fixed name in its own package.

A crate hub's binary lives in a spoke repository whose runfiles path holds the
crate's version and Bazel's canonical repository name. This rule gives it a
path in this package that names neither, so a dependant finds it in runfiles
at `_main/<package>/<target name>`.
"""

def _fixed_name_executable_impl(ctx):
    out = ctx.actions.declare_file(ctx.label.name)
    ctx.actions.symlink(
        output = out,
        target_file = ctx.executable.binary,
        is_executable = True,
    )
    runfiles = ctx.runfiles(files = [out]).merge(ctx.attr.binary[DefaultInfo].default_runfiles)
    return [DefaultInfo(executable = out, files = depset([out]), runfiles = runfiles)]

fixed_name_executable = rule(
    implementation = _fixed_name_executable_impl,
    doc = "Exposes `binary` as an executable named after this target, in this package.",
    attrs = {
        "binary": attr.label(
            doc = "The executable to expose.",
            executable = True,
            cfg = "target",
            mandatory = True,
        ),
    },
    executable = True,
)
