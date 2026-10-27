"""Command line entry point."""

from __future__ import annotations

import argparse
import logging

from dotenv import load_dotenv

from job_agent.config import load_config
from job_agent.db.store import Store
from job_agent.sources.runner import discover
from job_agent.scoring.service import score_discovered
from job_agent.tailoring.service import tailor_queued
from job_agent.apply.service import apply_approved, open_manual
from job_agent.scheduler import digest_text, run_once, run_scheduler, send_digest


def main() -> None:
    parser = argparse.ArgumentParser(prog="job-agent")
    parser.add_argument("--config", default="preferences.yaml")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("command", choices=["check-config", "init-db", "discover", "score", "tailor", "apply", "open-manual", "digest", "run-once", "scheduler"])
    parser.add_argument("job_id", nargs="?")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(args.config)
        load_dotenv(config.config_path.parent / ".env")
        if args.command == "check-config":
            print(f"Configuration valid: {config.config_path}")
            print(f"Resume: {config.profile.resume_path}")
            print(f"Database: {config.database_path}")
        elif args.command == "init-db":
            Store(config.database_path).initialize()
            print(f"Database initialized: {config.database_path}")
        elif args.command == "discover":
            store = Store(config.database_path)
            store.initialize()
            counts = discover(config, store)
            for source, count in counts.items():
                print(f"{source}: {count} new jobs")
        elif args.command == "score":
            store = Store(config.database_path)
            store.initialize()
            counts = score_discovered(config, store)
            print(" ".join(f"{key}={value}" for key, value in counts.items()))
        elif args.command == "tailor":
            store = Store(config.database_path)
            store.initialize()
            counts = tailor_queued(config, store)
            print(" ".join(f"{key}={value}" for key, value in counts.items()))
        elif args.command == "apply":
            counts = apply_approved(config, Store(config.database_path))
            print(" ".join(f"{key}={value}" for key, value in counts.items()))
        elif args.command == "open-manual":
            if not args.job_id:
                parser.error("open-manual requires a job ID")
            open_manual(config, Store(config.database_path), args.job_id)
        elif args.command == "digest":
            send_digest(digest_text(Store(config.database_path)))
        elif args.command == "run-once":
            run_once(config)
        elif args.command == "scheduler":
            run_scheduler(config)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    except KeyError as exc:
        parser.exit(1, f"Error: {exc.args[0]}\n")


if __name__ == "__main__":
    main()

