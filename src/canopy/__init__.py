"""
Canopy: a reusable agentic workflow harness.

Runs ticket-driven work as a graph of validated stages: model stages
driven by procedure skills, check stages that run real commands, and
gates that loop failed work back for another attempt, bounded and
journaled so a run can resume after an interruption.

A ticket is the request a run carries: a short description of the work
(usually a project issue), optional labels, and an optional effort
estimate. Canopy is project-agnostic: the workflow documents, the
skills, and the responder command are per-project; the harness is not.
"""
