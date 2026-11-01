import faulthandler, sys, threading, time
faulthandler.dump_traceback_later(10, repeat=False, exit=True)

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI

with create_pipe_input() as pipe:
    tui = ChatTUI(AppState(), workspace="/tmp/project", input_device=pipe, output_device=DummyOutput())
    pipe.send_text("/help\r")
    result_holder = {}
    runner = threading.Thread(target=lambda: result_holder.setdefault("r", tui.run_once()), daemon=True)
    runner.start()
    runner.join(60)
    print("result:", result_holder.get("r"), "alive:", runner.is_alive(), flush=True)
