"""Cron metadata for a recipe — never a live job.

This module only builds the spec and the command the owner would run. It does
not touch the cron store, enable anything, or fire a turn.
"""
from __future__ import annotations

import shlex

from recipes.spec import RecipeSpec


def build_cron_job_spec(spec: RecipeSpec) -> dict:
    """A cron job spec that asks the agent to run the recipe CLI.

    Schedule, name, delivery and the exact recipe command are data; the
    caller (an owner command or a future scheduler integration) decides
    whether to create the job.
    """
    command = f"qubit recipe run {spec.id} --json"
    return {
        "name": spec.id.replace("_", "-"),
        "schedule": spec.default_schedule,
        "prompt": (
            f"Run `{command}` and deliver the brief text unchanged. "
            "Do not invent data and do not perform any action."),
        "deliver": "local",
        "recipe": spec.id,
        "command": command,
    }


def cron_create_command(spec: RecipeSpec) -> str:
    """The single owner-facing command that would create (not enable) the job."""
    job = build_cron_job_spec(spec)
    return " ".join((
        "qubit cron create",
        shlex.quote(job["schedule"]),
        "--name", shlex.quote(job["name"]),
        "--prompt", shlex.quote(job["prompt"]),
        "--deliver", shlex.quote(job["deliver"]),
    ))
