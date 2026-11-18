"""인증 경계에서 주입된 owner만 사용하는 원격 학습·메모 제품 서비스."""
from .remote_annotation_consents import PostgresAnnotationConsentStore
from .remote_annotation_recovery_routes import AnnotationRecoveryService
from .remote_annotations import PostgresRemoteAnnotationStore
from .remote_learning import PostgresRemoteLearningStore


class RemoteAnnotationProducts(AnnotationRecoveryService):
    def __init__(self, pool, service_id):
        super().__init__(PostgresRemoteAnnotationStore(pool), service_id)
        self.learning = PostgresRemoteLearningStore(pool)
        self.consents = PostgresAnnotationConsentStore(pool)

    def create(self, owner, context, key, value):
        return self.store.create(owner, self.service_id, context, key, value,
                                 require_learning=True, require_storage_consent=True)

    def update(self, owner, context, annotation_id, body, step, revision):
        return self.store.update_annotation(owner, context, annotation_id, body=body, step=step,
                                            expected_revision=revision, require_storage_consent=True)
