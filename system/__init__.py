"""The system layer — what serves 4truck the operator, and nobody else.

Everything here is reachable only through ``/system/*`` behind
``require_system_owner``; no ``can_*`` flag names it, no plan sells it,
no customer role can be granted it, and Team Management cannot see it.
It exists to watch and, when it must, hold the customer layers — so it
sits ABOVE them: ``system/`` may import ``capabilities``, ``adapters``
and ``infra``; none of those may import ``system``.  That direction is
enforced in ``tests/test_layer_boundaries.py``, and it is what lets
this layer run as its own process on its own machine one day: the
customer processes ship without it, and it reads the customer layers
through Postgres and Redis, never through their memory.

Not to be confused with ``capabilities/platform/``, which holds the
DUAL-audience money domains (billing: the operator's endpoints and the
customer's own card page).  A domain a customer can touch is not a
system service, however much of it faces the operator.

Members:
  security                  the request ledger, the detector, the holds,
                            the nightly watch, the owner notice
  capacity                  the sampler, request metering, capacity alerts
  watchdog                  the machinery watchdog (watches the WORK)
  market_intel              market-intel rollups over shared work orders
  vendor_directory          the global vendor directory + reviews
  part_directory            the global part directory
  service_task_library      the shared service-task vocabulary
  service_assembly_library  the shared service-assembly library
"""
