from uuid import uuid4

from app.services.notification_service import refresh_operational_notifications


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value


class _FakeSession:
    def __init__(self):
        self.statements = []
        self._results = [_ScalarResult(2), _ScalarResult(3), _ScalarResult(None)]
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.statements.append((str(statement), params))
        return self._results.pop(0)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_refresh_runs_both_role_scoped_evaluators_and_restores_user_context():
    db = _FakeSession()
    user_id = uuid4()

    inserted = refresh_operational_notifications(
        db,
        recipient_user_id=user_id,
    )

    assert inserted == 5
    assert db.commits == 1
    assert db.rollbacks == 0
    assert "emit_department_manager_sla_notifications" in db.statements[0][0]
    assert "emit_company_admin_operational_notifications" in db.statements[1][0]
    assert "set_config('app.user_id'" in db.statements[2][0]
    assert db.statements[2][1] == {"user_id": str(user_id)}
