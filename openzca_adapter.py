import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


DEFAULT_OPENZCA_BIN = "/home/vtst/.nvm/versions/node/v22.22.2/bin/openzca"
DEFAULT_NODE_BIN = "/home/vtst/.nvm/versions/node/v22.22.2/bin/node"


@dataclass
class OpenZcaCommandResult:
    stdout: str
    stderr: str
    returncode: int
    command: list[str]


class OpenZcaClient:
    def __init__(self, binary_path: Optional[str] = None, profile: Optional[str] = None):
        self.binary_path = binary_path or os.environ.get("OPENZCA_BIN", DEFAULT_OPENZCA_BIN)
        self.node_path = os.environ.get("OPENZCA_NODE_BIN", self._resolve_node_path())
        self.profile = profile or os.environ.get("OPENZCA_PROFILE")

    def _resolve_node_path(self) -> str:
        binary_dir = Path(self.binary_path).resolve().parent
        sibling_node = binary_dir / "node"
        if sibling_node.exists():
            return str(sibling_node)
        return DEFAULT_NODE_BIN

    def _build_command(self, args: list[str]) -> list[str]:
        command = [self.node_path, self.binary_path]
        if self.profile:
            command.extend(["--profile", self.profile])
        command.extend(args)
        return command

    def run(self, args: list[str]) -> OpenZcaCommandResult:
        command = self._build_command(args)
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
        return OpenZcaCommandResult(
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
            returncode=proc.returncode,
            command=command,
        )

    def run_checked(self, args: list[str]) -> str:
        result = self.run(args)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr or result.stdout or f"openzca failed with code {result.returncode}"
            )
        return result.stdout

    def auth_status(self) -> dict[str, Any]:
        result = self.run(["auth", "status", "--json"])
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)

        output = self.run_checked(["auth", "status"])
        return self._parse_auth_status_output(output)

    def _parse_auth_status_output(self, output: str) -> dict[str, Any]:
        parsed: dict[str, Any] = {}
        for key, value in re.findall(r"(\w+):\s*'([^']*)'", output):
            parsed[key] = value
        for key, value in re.findall(r"(\w+):\s*(true|false)", output, flags=re.IGNORECASE):
            parsed[key] = value.lower() == "true"
        return parsed

    def send_text(self, thread_id: str, message: str, group: bool = True) -> str:
        result = self.send_text_detailed(thread_id, message, group=group)
        if result.returncode != 0:
            raise RuntimeError(
                result.stderr or result.stdout or f"openzca failed with code {result.returncode}"
            )
        return result.stdout

    def send_text_detailed(self, thread_id: str, message: str, group: bool = True) -> OpenZcaCommandResult:
        args = ["msg", "send", thread_id, message]
        if group:
            args.append("--group")
        return self.run(args)

    def send_image(
        self,
        thread_id: str,
        image_path: str,
        message: Optional[str] = None,
        group: bool = True,
    ) -> str:
        args = ["msg", "image", thread_id, image_path]
        if message:
            args.extend(["--message", message])
        if group:
            args.append("--group")
        return self.run_checked(args)

    def send_file(self, thread_id: str, file_path: str, group: bool = True) -> str:
        args = ["msg", "upload", file_path, thread_id]
        if group:
            args.append("--group")
        return self.run_checked(args)
