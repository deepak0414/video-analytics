"""Video Analytics — "Ctrl-F for Video".

Package root. Submodules:
  contracts/  — pydantic schemas (the data contracts)
  configuration — config loading (roles + hardware profile)
  runtime/    — model loading/lifecycle (used by in-process adapters + serving)
  sources/    — video acquisition (youtube, local) -> ResolvedVideo
  media/      — frame sampling / decode helpers
  roles/      — abstract role interfaces (Protocols)
  adapters/   — concrete backends per role (inproc / http / cloud)
  storage/    — vector + structured (catalog) stores
  pipeline/   — ingest + query orchestration
"""

__version__ = "0.1.0"
