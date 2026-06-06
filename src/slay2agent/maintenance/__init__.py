"""Offline skill maintenance pipeline (F-013).

Two CLI-triggered phases, run between play sessions — never during inference:

    slay2agent analyze   Phase 1: review every run's reconstructed trajectory
                         and write a per-run ``failure_report.json``.
    slay2agent distill   Phase 2: cluster recurring failure reasons, then
                         create or improve skills in the read-only library.

The play-time loop only *reads* the skill library; all writes happen here.
"""
