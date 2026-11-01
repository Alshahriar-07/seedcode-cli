import faulthandler, os, sys
sys.path.insert(0, os.getcwd())
faulthandler.dump_traceback_later(15, exit=True)
import pytest
pytest.main(["-q", "tests/test_v825_tui.py::test_repeated_runs_reuse_one_application_object"])
