"""Domain errors translated to HTTP responses by the application boundary."""


class DispatchError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
