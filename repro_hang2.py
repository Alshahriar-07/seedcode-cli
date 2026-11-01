import faulthandler, sys, threading, time
faulthandler.dump_traceback_later(12, repeat=False, exit=True)

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from seedcode.ui.state import AppState
from seedcode.ui.tui import ChatTUI

with create_pipe_input() as pipe:
    tui = ChatTUI(AppState(), workspace="/tmp/project", input_device=pipe, output_device=DummyOutput())
    result_holder = {}
    runner = threading.Thread(target=lambda: result_holder.setdefault("r", tui.run_once()), daemon=True)
    runner.start()
    time.sleep(0.5)
    pipe.send_text("/help\r")
    runner.join(30)
    print("result:", result_holder.get("r"), "alive:", runner.is_alive(), flush=True)
