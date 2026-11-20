"""P0 stub extraction. Runs in a child process: `python -I -m c2b_cad_worker.extract_stub <file>`.

It proves the sandbox image works (OpenCascade imports, the STEP reader can open the file) and
returns a placeholder result. Real XCAF extraction arrives in P2.
"""

import json
import os
import sys
from importlib import metadata

try:  # Linux only: cap CPU seconds as a second line of defence behind the parent's timeout.
    import resource

    _t = int(os.environ.get("C2B_CAD_TIMEOUT_S", "120"))
    resource.setrlimit(resource.RLIMIT_CPU, (_t, _t + 5))
except ImportError:
    pass


def main(path: str) -> dict[str, object]:
    from OCP.IFSelect import IFSelect_ReturnStatus
    from OCP.STEPControl import STEPControl_Reader

    size = os.path.getsize(path)
    reader = STEPControl_Reader()
    status = reader.ReadFile(path)
    read_status = {
        IFSelect_ReturnStatus.IFSelect_RetDone: "done",
        IFSelect_ReturnStatus.IFSelect_RetVoid: "void",
        IFSelect_ReturnStatus.IFSelect_RetError: "error",
        IFSelect_ReturnStatus.IFSelect_RetFail: "fail",
        IFSelect_ReturnStatus.IFSelect_RetStop: "stop",
    }.get(status, str(status))
    return {
        "status": "stub",
        "message": "placeholder extraction",
        "ocp_version": metadata.version("cadquery-ocp"),
        "cadquery_version": metadata.version("cadquery"),
        "bytes": size,
        "step_read_status": read_status,
    }


if __name__ == "__main__":
    print(json.dumps(main(sys.argv[1])))
