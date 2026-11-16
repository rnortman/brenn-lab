- Another repository can depend on brenn-lab as a Bazel module and declare
  its own runner with `lab_binary` from `@brenn_lab//brenn_lab:defs.bzl`. A
  script that runner runs can import the other repository's own Python and
  its third-party packages. The synthetic run builder and its stand-in reader
  (`//brenn_lab/testing:scenes`, `:export_stand_in`) and the speech clip are
  public, so that repository's tests can align a synthetic run through
  brenn-lab's own code. A synthetic capture can place the sound at a
  different delay on each channel. `requirements.in` is exported, so that
  repository's pin test can hold a package both pin at brenn-lab's version.
- A run's manifest has a new key, `builder`: the repository that built the
  runner and its revision, when that is not brenn-lab, or `null`. The
  repository's workspace status command supplies it through three optional
  build-stamp keys. `repositories` is unchanged, and the layout stays 5.
