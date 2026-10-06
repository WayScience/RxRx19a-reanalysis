"""
CLI for ReRx
"""

import json
import shutil
import sys

import fire

from rerx.main import show_message
from rerx.runs import PROTECTED, Run, list_runs, validate_run_id
from rerx.storage import load_storage_config


class RunsCLI:
    """
    Run inspection and cleanup commands (plan.md section 26).
    """

    def list(self) -> None:
        """
        List runs found under the configured datasets root.
        """
        storage = load_storage_config()
        listing = list_runs(storage.peta_root / "datasets")
        if not listing.runs:
            print("no runs found")
            return
        for run in listing.runs:
            print(f"{run.run_id}\t{run.status()}")

    def inspect(self, run_id: str) -> None:
        """
        Print a run's status and run.json contents.

        Args:
            run_id (str):
                Exact run ID to inspect.
        """
        validate_run_id(run_id)
        storage = load_storage_config()
        run_dir = storage.peta_root / "datasets" / run_id
        if not run_dir.is_dir():
            print(f"no such run: {run_id}", file=sys.stderr)
            raise SystemExit(1)
        run = Run(run_id=run_id, root=run_dir)
        print(f"run_id: {run.run_id}")
        print(f"status: {run.status()}")
        try:
            print(json.dumps(run.read_run_json(), indent=2, sort_keys=True))
        except FileNotFoundError:
            print("(no run.json yet)")

    def delete(self, run_id: str, dry_run: bool = True, yes: bool = False) -> None:
        """
        Delete a run directory by exact run ID (plan.md section 26).

        Refuses to delete `_PROTECTED` runs. Requires `--yes` (in addition
        to `--dry-run=False`) to actually remove anything.

        Args:
            run_id (str):
                Exact run ID to delete. Wildcards are never accepted.
            dry_run (bool):
                When true (the default), only print what would be deleted.
            yes (bool):
                Must be set to actually delete a run directory.
        """
        validate_run_id(run_id)
        storage = load_storage_config()
        run_dir = storage.peta_root / "datasets" / run_id
        if not run_dir.is_dir():
            print(f"no such run: {run_id}", file=sys.stderr)
            raise SystemExit(1)
        run = Run(run_id=run_id, root=run_dir)
        if run.has_marker(PROTECTED):
            print(f"refusing to delete protected run: {run_id}", file=sys.stderr)
            raise SystemExit(1)
        if dry_run or not yes:
            print(f"would delete: {run_dir}")
            if not yes:
                print("(pass --dry-run=False --yes to actually delete)")
            return
        shutil.rmtree(run_dir)
        print(f"deleted: {run_dir}")

    def protect(self, run_id: str) -> None:
        """
        Mark a run `_PROTECTED` so `runs delete` refuses it by default.

        Args:
            run_id (str):
                Exact run ID to protect.
        """
        validate_run_id(run_id)
        storage = load_storage_config()
        run_dir = storage.peta_root / "datasets" / run_id
        if not run_dir.is_dir():
            print(f"no such run: {run_id}", file=sys.stderr)
            raise SystemExit(1)
        run = Run(run_id=run_id, root=run_dir)
        run.set_marker(PROTECTED)
        print(f"protected: {run_id}")


class ReRxCLI:
    def __init__(self) -> None:
        self.runs = RunsCLI()

    def show_message(
        self,
        message: str = "Hello, world!",
    ) -> None:
        """
        CLI interface for show_message.

        Args:
            message (str):
                The message to print.
                Defaults to 'Hello, world!'.

        """
        print(show_message(message=message))


def trigger() -> None:
    """
    Trigger the CLI to run.
    """
    fire.Fire(ReRxCLI)
