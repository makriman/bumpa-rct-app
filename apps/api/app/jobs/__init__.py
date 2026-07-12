"""Background process entrypoints.

The local build executes synchronous provider jobs in-process. These long-running entrypoints keep
the deployment contract stable until Redis-backed dispatch is enabled.
"""
