"""The one clock every bound the product judges reads (#257).

`now()` is the float of epoch seconds in the file ATTAVO_CLOCK_FILE names
when that variable is set, and the machine's own `time.time()` otherwise.
The worker, the pass and the doctor are separate processes, so the clock
is a file every one of them reads, not an object passed in. Stamps the
product writes (`at` on records, markers and events) are facts, not
judgments, and stay on the real clock."""
from __future__ import annotations

import os
import time


def now() -> float:
    path = os.environ.get("ATTAVO_CLOCK_FILE")
    if not path:
        return time.time()
    with open(path) as f:
        return float(f.read().strip())
