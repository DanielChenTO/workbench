"""Unit tests for workbench.executor — zero-output failure detection."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from workbench.exceptions import ExecutorError
from workbench.executor import run_opencode


class TestZeroOutputDetection:
    """Tests for silent failure detection when opencode produces no output."""

    @pytest.fixture(autouse=True)
    def _patch_settings(self, tmp_path: Path):
        """Patch settings for test isolation."""
        with patch("workbench.executor.settings") as mock_settings:
            mock_settings.task_timeout = 30
            mock_settings.opencode_bin = "echo"
            mock_settings.opencode_model = None
            yield mock_settings

    def _make_mock_process(self, stdout: str, stderr: str, returncode: int):
        """Create a mock asyncio subprocess."""
        proc = MagicMock()
        proc.returncode = returncode
        proc.pid = 12345

        # Create mock streams that yield lines then stop
        stdout_lines = [line.encode() + b"\n" for line in stdout.split("\n") if line]
        stderr_lines = [line.encode() + b"\n" for line in stderr.split("\n") if line]

        stdout_reader = MagicMock()
        stderr_reader = MagicMock()

        stdout_reader.readline = AsyncMock(side_effect=stdout_lines + [b""])
        stderr_reader.readline = AsyncMock(side_effect=stderr_lines + [b""])

        proc.stdout = stdout_reader
        proc.stderr = stderr_reader
        proc.wait = AsyncMock(return_value=returncode)

        return proc

    def test_zero_output_raises_executor_error(self, tmp_path: Path):
        """opencode exiting with rc=0 but empty stdout should raise ExecutorError."""
        proc = self._make_mock_process(stdout="", stderr="some warning", returncode=0)

        with patch("workbench.executor.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.timeout = asyncio.timeout
            mock_asyncio.gather = asyncio.gather
            mock_asyncio.StreamReader = asyncio.StreamReader

            with pytest.raises(ExecutorError, match="no output"):
                asyncio.get_event_loop().run_until_complete(
                    run_opencode("test prompt", tmp_path)
                )

    def test_nonempty_output_succeeds(self, tmp_path: Path):
        """opencode exiting with rc=0 and non-empty stdout should succeed."""
        proc = self._make_mock_process(
            stdout="Agent completed the task successfully.",
            stderr="",
            returncode=0,
        )

        with patch("workbench.executor.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.timeout = asyncio.timeout
            mock_asyncio.gather = asyncio.gather
            mock_asyncio.StreamReader = asyncio.StreamReader

            result = asyncio.get_event_loop().run_until_complete(
                run_opencode("test prompt", tmp_path)
            )
            assert "completed the task" in result

    def test_nonzero_exit_still_raises(self, tmp_path: Path):
        """opencode exiting with non-zero rc should raise ExecutorError regardless of output."""
        proc = self._make_mock_process(stdout="", stderr="fatal error", returncode=1)

        with patch("workbench.executor.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.timeout = asyncio.timeout
            mock_asyncio.gather = asyncio.gather
            mock_asyncio.StreamReader = asyncio.StreamReader

            with pytest.raises(ExecutorError, match="exited with code 1"):
                asyncio.get_event_loop().run_until_complete(
                    run_opencode("test prompt", tmp_path)
                )

    def test_whitespace_only_output_raises(self, tmp_path: Path):
        """opencode producing only whitespace should be treated as zero output."""
        proc = self._make_mock_process(stdout="   \n  \n  ", stderr="", returncode=0)

        with patch("workbench.executor.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.timeout = asyncio.timeout
            mock_asyncio.gather = asyncio.gather
            mock_asyncio.StreamReader = asyncio.StreamReader

            with pytest.raises(ExecutorError, match="no output"):
                asyncio.get_event_loop().run_until_complete(
                    run_opencode("test prompt", tmp_path)
                )
