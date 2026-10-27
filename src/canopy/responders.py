"""
Responders: how the harness talks to a model backend.

The runner needs exactly one capability from a backend: take a prompt,
return a response string. Anything with that shape — a scripted function
in tests, a CLI that wraps a model API — is a responder. The module
ships one real responder, :class:`CommandResponder`, which runs a
command with the prompt on stdin and answers with its stdout.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandResponder:
    """
    Answer prompts by running a command with the prompt on stdin.

    The command's stdout is the response. A non-zero exit is a failed
    stage attempt, not a harness crash; a command that hangs is
    terminated at ``timeout_s`` seconds.

    Attributes
    ----------
    command : tuple[str, ...]
        The argv to run for every prompt.
    timeout_s : int
        Seconds to wait before the command is terminated.
    """

    command: tuple[str, ...]
    timeout_s: int = 900

    def __call__(self, prompt: str) -> str:
        """
        Run the command and return its response.

        Parameters
        ----------
        prompt : str
            The prompt to send, on the command's stdin.

        Returns
        -------
        str
            The command's stdout, without a trailing newline.

        Raises
        ------
        RuntimeError
            When the command exits non-zero or times out.
        """
        try:
            completed = subprocess.run(
                self.command,
                input=prompt,
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(
                f"responder command {self.command[0]!r} timed out "
                f"after {self.timeout_s}s"
            ) from error
        except OSError as error:
            raise RuntimeError(
                f"responder command {self.command[0]!r} could not start: {error}"
            ) from error
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "no stderr"
            raise RuntimeError(
                f"responder command {self.command[0]!r} failed "
                f"with exit {completed.returncode}: {detail}"
            )
        return completed.stdout.rstrip("\n")
