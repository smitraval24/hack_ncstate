import datetime

from hello.extensions import db


class Incident(db.Model):
    """Stores incident records for the self-healing pipeline."""

    __tablename__ = "incidents"

    id = db.Column(db.Integer, primary_key=True)

    # When the incident was first detected.
    detected_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        nullable=False,
    )

    # Fault code produced by the analyzer, e.g. FAULT_SQL_INJECTION_TEST.
    error_code = db.Column(db.String(128), nullable=False, index=True)

    # Free-text description of observed symptoms.
    symptoms = db.Column(db.Text, nullable=False, default="")

    # Structured breadcrumbs / log markers (stored as JSON text).
    breadcrumbs = db.Column(db.Text, nullable=False, default="[]")

    # Root cause determined by RAG + LLM reasoning.
    root_cause = db.Column(db.Text, nullable=True)

    # Remediation action that was applied.
    remediation = db.Column(db.Text, nullable=True)

    # Verification result after remediation.
    verification = db.Column(db.Text, nullable=True)

    # Whether the remediation was successful.
    resolved = db.Column(db.Boolean, default=False, nullable=False)

    # RAG retrieval metadata (JSON blob from Backboard).
    rag_query = db.Column(db.Text, nullable=True)
    rag_response = db.Column(db.Text, nullable=True)
    rag_confidence = db.Column(db.Float, nullable=True)

    # Backboard document ID assigned when this incident is indexed.
    backboard_doc_id = db.Column(db.String(256), nullable=True)

    updated_at = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
        nullable=False,
    )

    def to_dict(self):
        """Serialise for JSON responses."""
        return {
            "id": self.id,
            "detected_at": self.detected_at.isoformat(),
            "error_code": self.error_code,
            "symptoms": self.symptoms,
            "breadcrumbs": self.breadcrumbs,
            "root_cause": self.root_cause,
            "remediation": self.remediation,
            "verification": self.verification,
            "resolved": self.resolved,
            "rag_query": self.rag_query,
            "rag_response": self.rag_response,
            "rag_confidence": self.rag_confidence,
            "backboard_doc_id": self.backboard_doc_id,
            "updated_at": self.updated_at.isoformat(),
        }

    def to_document_content(self):
        """Format this incident as a text document for Backboard indexing."""
        return (
            f"IncidentID: {self.id}\n"
            f"ErrorCode: {self.error_code}\n"
            f"Symptoms: {self.symptoms}\n"
            f"Breadcrumbs: {self.breadcrumbs}\n"
            f"RootCause: {self.root_cause}\n"
            f"Remediation: {self.remediation}\n"
            f"Verification: {self.verification}\n"
            f"Resolved: {self.resolved}\n"
        )

    def __repr__(self):
        return f"<Incident {self.id} [{self.error_code}]>"
