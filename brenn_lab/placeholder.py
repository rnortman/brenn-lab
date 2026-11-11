"""A placeholder that proves the build, test, lint and type lanes end to end.

It goes through numpy so the locked dependency, its type stubs and the
hermetic interpreter are all exercised. The first harness change deletes it.
"""

from collections.abc import Sequence

import numpy as np
import numpy.typing as npt


def mean_of(values: Sequence[float]) -> float:
    """The arithmetic mean of `values`."""
    arr: npt.NDArray[np.float64] = np.asarray(values, dtype=np.float64)
    return float(arr.mean())
