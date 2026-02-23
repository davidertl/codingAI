class OrchestratorError(Exception):
    pass


class OrchestratorValidationError(OrchestratorError):
    pass


class SecurityPolicyViolationError(OrchestratorError):
    pass


class ExecutionBoundaryError(OrchestratorError):
    pass


class AbortRunError(OrchestratorError):
    pass
