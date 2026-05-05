#!/usr/bin/env python3
"""Create a pg_dump tar backup for the Postgres translation database."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urlparse

import psycopg2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a pg_dump tar backup for the translation Postgres DB.",
    )
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string. Defaults to DATABASE_URL, then .codex/config.toml.",
    )
    parser.add_argument(
        "--output-dir",
        default="_working/backups/postgres",
        help="Directory for backup tar files.",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Optional output filename. Defaults to <database>-<timestamp>.tar.",
    )
    parser.add_argument(
        "--pg-dump",
        default=None,
        help="Optional explicit pg_dump executable path.",
    )
    parser.add_argument(
        "--docker-image",
        default=None,
        help="Optional Docker image for pg_dump fallback. Defaults to postgres:<server-major>.",
    )
    parser.add_argument(
        "--docker-network",
        default=None,
        help="Optional Docker network argument, for example host.",
    )
    parser.add_argument(
        "--no-owner",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Dump without ownership commands for easier restore under another role.",
    )
    parser.add_argument(
        "--no-privileges",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Dump without GRANT/REVOKE commands.",
    )
    return parser.parse_args()


def load_database_url_from_codex_config() -> str | None:
    config_path = Path(".codex/config.toml")
    if not config_path.exists():
        return None
    config = tomllib.loads(config_path.read_text())
    try:
        return config["mcp_servers"]["postgres"]["env"]["DATABASE_URI"]
    except KeyError:
        return None


def database_name(database_url: str) -> str:
    parsed = urlparse(database_url)
    name = parsed.path.rsplit("/", 1)[-1]
    return name or "postgres"


def connection_env(database_url: str) -> dict[str, str]:
    parsed = urlparse(database_url)
    return {
        "PGHOST": parsed.hostname or "localhost",
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGDATABASE": database_name(database_url),
    }


def server_major_version(database_url: str) -> int:
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cursor:
            cursor.execute("show server_version_num")
            version_num = int(cursor.fetchone()[0])
    return version_num // 10000


def pg_dump_major_version(pg_dump_path: str) -> int | None:
    try:
        result = subprocess.run(
            [pg_dump_path, "--version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None

    # Examples:
    # pg_dump (PostgreSQL) 15.15
    # pg_dump (PostgreSQL) 18.3 (...)
    for token in result.stdout.split():
        if token and token[0].isdigit():
            return int(token.split(".", 1)[0])
    return None


def choose_local_pg_dump(database_url: str, explicit_pg_dump: str | None) -> str | None:
    if explicit_pg_dump:
        return explicit_pg_dump

    server_major = server_major_version(database_url)
    versioned_path = Path(f"/usr/lib/postgresql/{server_major}/bin/pg_dump")
    if versioned_path.exists():
        return str(versioned_path)

    path_pg_dump = shutil.which("pg_dump")
    if path_pg_dump and (pg_dump_major_version(path_pg_dump) or 0) >= server_major:
        return path_pg_dump

    return None


def safe_backup_name(raw_name: str) -> str:
    allowed = []
    for char in raw_name:
        if char.isalnum() or char in ("-", "_", "."):
            allowed.append(char)
        else:
            allowed.append("_")
    return "".join(allowed).strip("._") or "postgres-backup"


def main() -> int:
    args = parse_args()
    database_url = args.database_url or load_database_url_from_codex_config()
    if not database_url:
        print(
            "Missing database URL. Set DATABASE_URL or pass --database-url.",
            file=sys.stderr,
        )
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    filename = args.name or f"{database_name(database_url)}-{timestamp}.tar"
    if not filename.endswith(".tar"):
        filename = f"{filename}.tar"
    output_path = output_dir / safe_backup_name(filename)

    pg_dump_env = os.environ.copy()
    pg_dump_env.update(connection_env(database_url))

    command = []
    local_pg_dump = choose_local_pg_dump(database_url, args.pg_dump)
    if local_pg_dump:
        command = [
            local_pg_dump,
            "--host",
            pg_dump_env["PGHOST"],
            "--port",
            pg_dump_env["PGPORT"],
            "--username",
            pg_dump_env["PGUSER"],
            "--dbname",
            pg_dump_env["PGDATABASE"],
            "--format=tar",
            "--file",
            str(output_path),
        ]
    else:
        docker = shutil.which("docker")
        if not docker:
            print(
                "No compatible pg_dump found and docker is not available.",
                file=sys.stderr,
            )
            return 2

        server_major = server_major_version(database_url)
        docker_image = args.docker_image or f"postgres:{server_major}"
        container_output = f"/backup/{output_path.name}"
        env_file = tempfile.NamedTemporaryFile(
            "w",
            prefix="pgdump-env-",
            suffix=".env",
            dir=output_dir,
            delete=False,
        )
        env_file_path = Path(env_file.name)
        try:
            for key in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE"):
                env_file.write(f"{key}={pg_dump_env[key]}\n")
            env_file.close()

            command = [
                docker,
                "run",
                "--rm",
                "--env-file",
                str(env_file_path.resolve()),
                "-v",
                f"{output_dir.resolve()}:/backup",
            ]
            if args.docker_network:
                command.extend(["--network", args.docker_network])
            command.extend(
                [
                    docker_image,
                    "pg_dump",
                    "--host",
                    pg_dump_env["PGHOST"],
                    "--port",
                    pg_dump_env["PGPORT"],
                    "--username",
                    pg_dump_env["PGUSER"],
                    "--dbname",
                    pg_dump_env["PGDATABASE"],
                    "--format=tar",
                    "--file",
                    container_output,
                ]
            )
        finally:
            # Remove after subprocess below; keep path variable alive.
            pass

    if args.no_owner:
        command.append("--no-owner")
    if args.no_privileges:
        command.append("--no-privileges")

    try:
        subprocess.run(command, check=True, env=pg_dump_env)
    except subprocess.CalledProcessError as exc:
        if output_path.exists() and output_path.stat().st_size == 0:
            output_path.unlink()
        print(f"pg_dump failed with exit code {exc.returncode}.", file=sys.stderr)
        return exc.returncode
    finally:
        for candidate in output_dir.glob("pgdump-env-*.env"):
            candidate.unlink(missing_ok=True)

    manifest_path = output_path.with_suffix(".json")
    manifest = {
        "created_at_utc": timestamp,
        "database": database_name(database_url),
        "format": "pg_dump tar",
        "backup_path": str(output_path),
        "size_bytes": output_path.stat().st_size,
        "restore_example": (
            f"pg_restore --clean --if-exists --dbname \"$DATABASE_URL\" {output_path}"
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"backup_path: {output_path}")
    print(f"manifest_path: {manifest_path}")
    print(f"size_bytes: {manifest['size_bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
