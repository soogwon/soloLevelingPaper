"""신규 유료 접수와 추가 provider 전송을 서로 독립적으로 제어한다."""

from dataclasses import dataclass


@dataclass(frozen=True)
class OperationControls:
    generation: int
    intake_enabled: bool
    dispatch_enabled: bool


class PostgresRemoteOperationControls:
    def __init__(self, pool):
        self._pool = pool

    def get(self) -> OperationControls | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                """SELECT generation,intake_enabled,dispatch_enabled
                   FROM server_dispatch_controls WHERE control_id='provider_dispatch'"""
            ).fetchone()
        return None if row is None else _view(row)

    def update(
        self, expected_generation: int, *, intake_enabled: bool, dispatch_enabled: bool
    ) -> OperationControls | None:
        if (
            type(expected_generation) is not int
            or expected_generation < 1
            or type(intake_enabled) is not bool
            or type(dispatch_enabled) is not bool
        ):
            raise ValueError("operation_control_input_invalid")
        with self._pool.connection() as conn:
            row = conn.execute(
                """UPDATE server_dispatch_controls
                   SET generation=generation+1,intake_enabled=%s,dispatch_enabled=%s,
                       updated_at=CURRENT_TIMESTAMP
                   WHERE control_id='provider_dispatch' AND generation=%s
                   RETURNING generation,intake_enabled,dispatch_enabled""",
                (intake_enabled, dispatch_enabled, expected_generation),
            ).fetchone()
        return None if row is None else _view(row)


def _view(row):
    return OperationControls(int(row["generation"]), bool(row["intake_enabled"]), bool(row["dispatch_enabled"]))
