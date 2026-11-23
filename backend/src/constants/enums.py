from enum import StrEnum

class Role(StrEnum):
    ADMIN = 'admin'
    ANALYST = 'analyst'
    VIEWER = 'viewer'

class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETE = 'complete'
    FAILED = 'failed'


class PairingState(StrEnum):
    RUNNING = 'running'
    COMPLETE = 'complete'

class Review(StrEnum):
    PENDING = 'pending'
    ACCEPTED = 'accepted'
    RETURNED = 'returned'
