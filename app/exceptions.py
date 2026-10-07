"""Domain errors. Each carries the HTTP status it should be rendered with."""


class AppError(Exception):
    status_code = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- Rejected before anything is stored -------------------------------------
class UnsupportedFileType(AppError):
    status_code = 415


class FileTooLarge(AppError):
    status_code = 413


class EmptyFile(AppError):
    status_code = 400


class InvalidCRS(AppError):
    status_code = 422


# --- Raised while processing a stored upload (file is marked FAILED) --------
class ProcessingError(AppError):
    status_code = 422


class InvalidArchive(ProcessingError):
    pass


class InvalidGeoFile(ProcessingError):
    pass


class MissingCRS(ProcessingError):
    pass
