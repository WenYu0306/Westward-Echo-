"""The package entry must fail loudly on an unsupported interpreter.

macOS ships ``python3`` 3.9. Running any script with it used to crash deep in
``src/output_guard.py`` with "unsupported operand type(s) for |: 'type' and
'NoneType'" — a PEP 604 error whose location says nothing about the real cause.
"""

import pytest

from src import MIN_PYTHON, check_python_version


class TestPythonVersionGuard:
    def test_current_interpreter_passes(self):
        check_python_version()          # the suite only runs on 3.11+

    def test_supported_version_passes(self):
        check_python_version((3, 11, 0, "final", 0))
        check_python_version((3, 12, 1, "final", 0))

    def test_old_version_raises_with_an_actionable_message(self):
        with pytest.raises(RuntimeError) as exc:
            check_python_version((3, 9, 6, "final", 0))
        message = str(exc.value)
        assert "3.11" in message            # what is required
        assert "3.9.6" in message           # what is running
        assert "python3.11" in message      # how to fix it

    def test_minimum_is_the_documented_runtime(self):
        assert MIN_PYTHON == (3, 11)
