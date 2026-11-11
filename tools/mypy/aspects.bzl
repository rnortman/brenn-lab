"""The mypy aspect, switched on by `--config=lint`."""

load("@rules_mypy//mypy:mypy.bzl", "mypy")

mypy_aspect = mypy(
    mypy_cli = "@@//tools/mypy:mypy_cli",
    mypy_ini = "@@//:mypy.ini",
)
