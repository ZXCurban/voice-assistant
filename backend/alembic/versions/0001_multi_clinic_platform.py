"""Multi-clinic voice assistant platform: initial schema.

Creates all 10 tenant-scoped tables with composite uniqueness, check
constraints and the partial unique index that prevents double booking.
Seed/demo data is NOT part of migrations (see app.db.seed_demo).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_multi_clinic_platform"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "clinics",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("address", sa.String(length=500), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("timezone", sa.Text(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "specialties",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "name", name="uq_specialties_clinic_name"),
    )
    op.create_index("ix_specialties_clinic_id", "specialties", ["clinic_id"])
    op.create_table(
        "departments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=150), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("floor", sa.SmallInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "name", name="uq_departments_clinic_name"),
    )
    op.create_index("ix_departments_clinic_id", "departments", ["clinic_id"])
    op.create_table(
        "rooms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("code", sa.String(length=30), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=True),
        sa.Column("floor", sa.SmallInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clinic_id", "code", name="uq_rooms_clinic_code"),
    )
    op.create_index("ix_rooms_clinic_id", "rooms", ["clinic_id"])
    op.create_table(
        "doctors",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("specialty_id", sa.Integer(), nullable=False),
        sa.Column("department_id", sa.Integer(), nullable=True),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["department_id"], ["departments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["specialty_id"], ["specialties.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_doctors_clinic_id", "doctors", ["clinic_id"])
    op.create_index(
        "ix_doctors_clinic_specialty_active", "doctors", ["clinic_id", "specialty_id", "active"]
    )
    op.create_index("ix_doctors_clinic_department", "doctors", ["clinic_id", "department_id"])
    op.create_table(
        "patients",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_patients_clinic_id", "patients", ["clinic_id"])
    op.create_index("ix_patients_clinic_phone", "patients", ["clinic_id", "phone"])
    op.create_table(
        "clinic_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_local", sa.Time(), nullable=False),
        sa.Column("end_local", sa.Time(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("start_local < end_local", name="ck_clinic_schedules_interval"),
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_clinic_schedules_weekday"),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_clinic_schedules_clinic_weekday", "clinic_schedules", ["clinic_id", "weekday"]
    )
    op.create_table(
        "doctor_schedules",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("doctor_id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("start_local", sa.Time(), nullable=False),
        sa.Column("end_local", sa.Time(), nullable=False),
        sa.Column("slot_minutes", sa.SmallInteger(), nullable=False, server_default="20"),
        sa.Column("room_id", sa.Integer(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("start_local < end_local", name="ck_doctor_schedules_interval"),
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_doctor_schedules_weekday"),
        sa.CheckConstraint(
            "slot_minutes IN (5, 10, 15, 20, 30, 45, 60)", name="ck_doctor_schedules_slot"
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_doctor_schedules_doctor_weekday", "doctor_schedules", ["doctor_id", "weekday"]
    )
    op.create_index(
        "ix_doctor_schedules_clinic_doctor", "doctor_schedules", ["clinic_id", "doctor_id"]
    )
    op.create_table(
        "schedule_exceptions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("doctor_id", sa.Integer(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("start_local", sa.Time(), nullable=True),
        sa.Column("end_local", sa.Time(), nullable=True),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("kind IN ('day_off', 'custom_hours')", name="ck_exceptions_kind"),
        sa.CheckConstraint(
            "(kind = 'day_off' AND start_local IS NULL AND end_local IS NULL) OR "
            "(kind = 'custom_hours' AND start_local IS NOT NULL AND "
            "end_local IS NOT NULL AND start_local < end_local)",
            name="ck_exceptions_times",
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_schedule_exceptions_clinic_id", "schedule_exceptions", ["clinic_id"])
    op.create_index(
        "uq_exceptions_clinic_date",
        "schedule_exceptions",
        ["clinic_id", "date"],
        unique=True,
        postgresql_where=sa.text("doctor_id IS NULL"),
        sqlite_where=sa.text("doctor_id IS NULL"),
    )
    op.create_index(
        "uq_exceptions_clinic_doctor_date",
        "schedule_exceptions",
        ["clinic_id", "doctor_id", "date"],
        unique=True,
        postgresql_where=sa.text("doctor_id IS NOT NULL"),
        sqlite_where=sa.text("doctor_id IS NOT NULL"),
    )
    op.create_index("ix_exceptions_doctor_date", "schedule_exceptions", ["doctor_id", "date"])
    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clinic_id", sa.Integer(), nullable=False),
        sa.Column("doctor_id", sa.Integer(), nullable=False),
        sa.Column("patient_id", sa.Integer(), nullable=False),
        sa.Column("room_id", sa.Integer(), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="booked"),
        sa.Column("reason", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()
        ),
        sa.CheckConstraint("ends_at > starts_at", name="ck_appointments_interval"),
        sa.CheckConstraint(
            "status IN ('booked', 'cancelled', 'completed')", name="ck_appointments_status"
        ),
        sa.ForeignKeyConstraint(["clinic_id"], ["clinics.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["doctor_id"], ["doctors.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["patient_id"], ["patients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["room_id"], ["rooms.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_appointments_booked_slot",
        "appointments",
        ["clinic_id", "doctor_id", "starts_at"],
        unique=True,
        postgresql_where=sa.text("status = 'booked'"),
        sqlite_where=sa.text("status = 'booked'"),
    )
    op.create_index("ix_appointments_doctor_starts", "appointments", ["doctor_id", "starts_at"])
    op.create_index("ix_appointments_patient_starts", "appointments", ["patient_id", "starts_at"])
    op.create_index("ix_appointments_clinic_starts", "appointments", ["clinic_id", "starts_at"])


def downgrade() -> None:
    op.drop_table("appointments")
    op.drop_table("schedule_exceptions")
    op.drop_table("doctor_schedules")
    op.drop_table("clinic_schedules")
    op.drop_table("patients")
    op.drop_table("doctors")
    op.drop_table("rooms")
    op.drop_table("departments")
    op.drop_table("specialties")
    op.drop_table("clinics")
