#!/usr/bin/env python3
"""re_exec.py — drive the live UnrealEditor via UE Python Remote Execution.

Reads Python statements from stdin, runs them inside the running editor, prints
the editor's stdout back. Requires: editor up with Remote Execution enabled, and
the loopback multicast route present (`sudo ip route add 239.0.0.1 dev lo`).

  echo 'import unreal; print(unreal.SystemLibrary.get_engine_version())' | re_exec.py
"""
import sys
import time

sys.path.append(
    "/home/corey/UnrealEngine/Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python"
)
import remote_execution as re  # noqa: E402

code = sys.stdin.read()
if not code.strip():
    print("ERR: no code on stdin")
    sys.exit(2)

ex = re.RemoteExecution(re.RemoteExecutionConfig())
ex.start()
node = None
for _ in range(80):  # ~20s discovery budget (game thread may be busy compiling)
    nodes = ex.remote_nodes
    if nodes:
        node = nodes[0]
        break
    time.sleep(0.25)

if not node:
    print("ERR: no RE node discovered (editor up? route added? game thread blocked?)")
    ex.stop()
    sys.exit(3)

sys.stderr.write("RE node: %s\n" % node["node_id"])
ex.open_command_connection(node["node_id"])
try:
    r = ex.run_command(
        code, unattended=True, exec_mode=re.MODE_EXEC_STATEMENT, raise_on_failure=False
    )
    print("success=%s" % r.get("success"))
    out = r.get("output") or []
    for line in out:
        if isinstance(line, dict):
            print(line.get("output", ""))
        else:
            print(line)
    if r.get("result"):
        print("result=%s" % r.get("result"))
finally:
    try:
        ex.close_command_connection()
    except Exception:
        pass
    ex.stop()
