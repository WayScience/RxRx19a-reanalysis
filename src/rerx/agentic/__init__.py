"""
Agentic workflow harness for ReRx.

Runs ticket-driven work as a graph of validated stages: model stages driven
by procedure skills, check stages that run real commands, and gates that
loop failed work back for another attempt, bounded and journaled so a run
can resume after an interruption.
"""