# Cross-service tests

Application-owned tests stay beside their code in `apps/api/tests` and the web test
tree. This directory holds provider contract fixtures and cross-service/load test
assets that do not belong to either application.

All fixtures are versioned, synthetic and safe to publish. Failure fixtures must
cover timeout, rate limiting, malformed response, partial/unavailable data and
unknown enum values. Never copy a production payload here, even after ad-hoc manual
redaction.
