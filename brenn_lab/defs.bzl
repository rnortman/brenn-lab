"""`lab_binary`: the experiment runner, declared in the caller's package."""

load("@rules_python//python:defs.bzl", "py_binary")

def lab_binary(name, deps = [], **kwargs):
    """The runner: a py_binary running brenn_lab's cli.py.

    It carries the pinned recorder, the sink client, the pinned frame-log
    reader and the build stamp. An experiment script it runs can import
    whatever `deps` carries, as well as brenn_lab itself.

    Args:
        name: the binary's name.
        deps: the caller's py_library targets, importable by a script.
        **kwargs: passed to py_binary.
    """
    py_binary(
        name = name,
        srcs = [Label("//brenn_lab:cli.py")],
        main = Label("//brenn_lab:cli.py"),
        data = [
            Label("//brenn_lab/playback/pulse_client:pulse-client"),
            Label("//tools/brenn_pod:frame-log"),
            Label("//tools/brenn_pod:pod-capture"),
            Label("//tools/buildinfo:build_info"),
        ],
        deps = [
            Label("//brenn_lab:align"),
            Label("//brenn_lab:assess"),
            Label("//brenn_lab:brenn_lab"),
            Label("//brenn_lab:cli"),
            Label("@rules_python//python/runfiles"),
        ] + deps,
        **kwargs
    )
