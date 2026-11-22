"""The orchestrator: owns every scan from queue to completion.

It plans chunks, sends them to the scanner, records what comes back and runs
the phases after probing. It serves no HTTP to the browser and sends no probe.
PostgreSQL is its only source of truth; broker messages only make it react
sooner (AD1).
"""
