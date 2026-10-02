"""Application errors returned to the chat page as JSON."""


class AppError(Exception):
    def __init__(self, status_code: int, message: str, status: str = "ERROR") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.status = status
