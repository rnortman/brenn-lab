"""py_pytest_test: a py_test whose main runs pytest over its own srcs."""

load("@rules_python//python:defs.bzl", "py_test")

_MAIN = Label("//tools/pytest:pytest_main.py")

def py_pytest_test(name, srcs, deps = [], args = [], **kwargs):
    """A py_test that runs pytest over `srcs`.

    Args:
      name: the target name.
      srcs: the test files; pytest collects from these and nothing else.
      deps: the libraries under test. pytest is added.
      args: extra pytest arguments, after the files.
      **kwargs: passed to py_test.
    """
    py_test(
        name = name,
        srcs = srcs + [_MAIN],
        main = _MAIN,
        args = ["$(rootpath %s)" % s for s in srcs] + args,
        deps = deps + ["@pypi_lab//pytest"],
        **kwargs
    )
